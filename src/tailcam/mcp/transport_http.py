"""Stateless Streamable HTTP transport mounted at ``/mcp``.

Remote agents (Claude API MCP connector, remote Codex, remote Hermes/OpenClaw)
connect over Tailscale Serve. The endpoint is **stateless**: it never issues an
``Mcp-Session-Id``, holds nothing between requests, and treats every POST as a
self-contained exchange. Concretely:

- There is no session to create, resume, or expire. A node restart, a config
  reload, or two requests served in any order change nothing, and an agent may
  call ``tools/call`` without replaying ``initialize`` first.
- The revision a request belongs to travels in the ``MCP-Protocol-Version``
  header instead of being remembered from the handshake. Absent, it is assumed
  to be ``2025-03-26`` (the spec's backwards-compatibility rule, and the case
  for ``initialize`` itself); unsupported, it is rejected with 400 rather than
  silently mis-served.
- An ``Mcp-Session-Id`` sent by a client written against a stateful server is
  ignored, not rejected, so those clients keep working unchanged.
- No server-initiated streams and no session teardown: GET and DELETE answer
  405 ``Allow: POST``.
- The caller's name for the audit trail is resolved per request (from this
  request's ``initialize``, else its ``User-Agent``) rather than from a
  handshake that happened on some earlier connection.

It is fail-closed: the caller is parsed with TailCam's principal parser and
unverified callers are rejected before any tool runs. Tools execute against the
node in-process while the server core enforces roles and confirmation and audits
every state-changing action with the real remote principal.

Responses are JSON by default and switch to a single Server-Sent-Events frame
when the client only accepts ``text/event-stream``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from tailcam.management.audit import AuditLog
from tailcam.mcp import SUPPORTED_PROTOCOL_VERSIONS
from tailcam.mcp.client import TailcamClient
from tailcam.mcp.protocol import (
    ASSUMED_PROTOCOL_VERSION,
    INVALID_REQUEST,
    PARSE_ERROR,
    PROTOCOL_VERSION_HEADER,
    client_name_from,
    error_response,
)
from tailcam.mcp.server import McpServer
from tailcam.security.principal import principal_from_request

router = APIRouter()

# The SPA page that documents/controls the MCP endpoint. /mcp itself is the
# JSON-RPC endpoint (and is excluded from the SPA catch-all), so a human who
# types /mcp is sent here instead of getting a bare protocol response.
_MCP_PAGE = "/agents"

_DISABLED_MESSAGE = (
    "the MCP HTTP endpoint is disabled on this node (enable it on the MCP page)"
)

# Stateless: responses are never a cacheable representation of a resource.
_NO_STORE = "no-store"


def _post_only() -> Response:
    """405 for the verbs a stateless endpoint has nothing to do with.

    There are no server-initiated SSE streams to GET and no session to DELETE.
    """

    return Response(status_code=405, headers={"Allow": "POST"})


def _http_disabled(request: Request) -> bool:
    """Runtime gate (not mount-time): the MCP settings page toggles this live."""
    mcp_cfg = request.app.state.ctx.config.mcp
    return not (mcp_cfg.enabled and mcp_cfg.http_enabled)


def _disabled_response() -> JSONResponse:
    return JSONResponse(
        error_response(None, INVALID_REQUEST, _DISABLED_MESSAGE), status_code=404
    )


@router.get("/mcp")
async def mcp_get(request: Request) -> Response:
    # A browser hitting /mcp wants the settings page, not a protocol response —
    # send it to the SPA tab (which lives at /agents because /mcp is reserved).
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(_MCP_PAGE, status_code=302)
    # Gated the same as POST so a disabled node doesn't advertise the endpoint.
    if _http_disabled(request):
        return _disabled_response()
    # We do not offer server-initiated SSE streams; advertise POST only.
    return _post_only()


@router.delete("/mcp")
async def mcp_delete(request: Request) -> Response:
    """Session teardown is meaningless here — there is no session to delete.

    The spec lets a server that does not support client-driven termination
    answer 405, which is exactly the stateless case.
    """
    if _http_disabled(request):
        return _disabled_response()
    return _post_only()


@router.post("/mcp")
async def mcp_post(request: Request) -> Response:
    if _http_disabled(request):
        return _disabled_response()
    principal = principal_from_request(request)
    if not principal.verified:
        return JSONResponse(
            error_response(None, INVALID_REQUEST, "a verified Tailscale principal is required"),
            status_code=401,
        )

    requested_version = request.headers.get(PROTOCOL_VERSION_HEADER)
    if requested_version is not None and requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
        supported = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
        return JSONResponse(
            error_response(
                None,
                INVALID_REQUEST,
                f"unsupported {PROTOCOL_VERSION_HEADER}: {requested_version!r} "
                f"(this server speaks {supported})",
            ),
            status_code=400,
        )
    # No header means a pre-2025-06-18 client, or the initialize that has not
    # negotiated yet. Either way the request still carries its own revision.
    version = requested_version or ASSUMED_PROTOCOL_VERSION

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(error_response(None, PARSE_ERROR, "parse error"), status_code=400)

    ctx = request.app.state.ctx
    client = TailcamClient.for_app(request.app)
    server = McpServer(
        client=client,
        principal=principal,
        config=ctx.config.mcp,
        transport="streamable_http",
        audit=AuditLog(ctx.store),
        client_name=_client_name(body, request),
        protocol_version=version,
    )

    try:
        if isinstance(body, list):
            responses = [r for m in body if (r := await _handle_one(server, m)) is not None]
            payload: Any = responses
            empty = not responses
        elif isinstance(body, dict):
            payload = await server.handle(body)
            empty = payload is None
        else:
            return JSONResponse(
                error_response(None, INVALID_REQUEST, "expected a JSON-RPC object"),
                status_code=400,
            )
    finally:
        await client.aclose()

    headers = {
        PROTOCOL_VERSION_HEADER: _answered_version(payload, version),
        "Cache-Control": _NO_STORE,
    }
    if empty:
        # Only notifications/responses were sent: acknowledge with no body.
        return Response(status_code=202, headers=headers)

    if _wants_sse_only(request):
        return _sse(payload, headers)
    return JSONResponse(payload, headers=headers)


async def _handle_one(server: McpServer, message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return error_response(None, INVALID_REQUEST, "expected a JSON-RPC object")
    return await server.handle(message)


def _client_name(body: Any, request: Request) -> str | None:
    """Name the caller for the audit trail without consulting any session.

    An ``initialize`` in *this* request is authoritative. Otherwise fall back to
    the User-Agent: a stateless server never saw the handshake, so that header is
    the only per-request identity a client offers.
    """

    messages = body if isinstance(body, list) else [body]
    for message in messages:
        if isinstance(message, dict) and (name := client_name_from(message)):
            return name
    return request.headers.get("user-agent", "").strip() or None


def _answered_version(payload: Any, fallback: str) -> str:
    """The revision this response actually speaks.

    For everything but ``initialize`` that is the revision the request declared.
    An ``initialize`` result carries the freshly negotiated one, which is what the
    client will send back in the header from then on — echo that instead.
    """

    for message in payload if isinstance(payload, list) else [payload]:
        if not isinstance(message, dict):
            continue
        result = message.get("result")
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            return result["protocolVersion"]
    return fallback


def _wants_sse_only(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/event-stream" in accept and "application/json" not in accept


def _sse(payload: Any, headers: dict[str, str]) -> Response:
    import json

    body = f"event: message\ndata: {json.dumps(payload, default=str)}\n\n"
    return Response(body, media_type="text/event-stream", headers=headers)
