"""Streamable HTTP transport mounted at ``/mcp``.

Remote agents (Claude API MCP connector, remote Codex, remote Hermes/OpenClaw)
connect over Tailscale Serve. This transport is fail-closed: it parses the caller
with TailCam's principal parser and rejects unverified callers before any tool
runs. Tools execute against the node in-process while the server core enforces
roles and confirmation and audits every state-changing action with the real
remote principal.

The server replies in stateless JSON mode by default and switches to a single
Server-Sent-Events frame when the client only accepts ``text/event-stream``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from tailcam.management.audit import AuditLog
from tailcam.mcp.client import TailcamClient
from tailcam.mcp.protocol import INVALID_REQUEST, PARSE_ERROR, error_response
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
    return Response(status_code=405, headers={"Allow": "POST"})


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

    if empty:
        # Only notifications/responses were sent: acknowledge with no body.
        return Response(status_code=202)

    if _wants_sse_only(request):
        return _sse(payload)
    return JSONResponse(payload)


async def _handle_one(server: McpServer, message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return error_response(None, INVALID_REQUEST, "expected a JSON-RPC object")
    return await server.handle(message)


def _wants_sse_only(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/event-stream" in accept and "application/json" not in accept


def _sse(payload: Any) -> Response:
    import json

    body = f"event: message\ndata: {json.dumps(payload, default=str)}\n\n"
    return Response(body, media_type="text/event-stream")
