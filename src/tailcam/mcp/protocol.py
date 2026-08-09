"""JSON-RPC 2.0 plumbing for the MCP server core.

The MCP wire protocol is line-delimited JSON-RPC 2.0 over stdio and POSTed
JSON-RPC over Streamable HTTP. This module owns the small set of framing helpers
and the protocol method names so the rest of the package never hand-builds
envelopes.

It also owns the two pieces a *stateless* server needs to read off each request
instead of off a remembered handshake: the negotiated protocol revision (the
``MCP-Protocol-Version`` header) and the client's announced name.
"""

from __future__ import annotations

from typing import Any

from tailcam.mcp import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS

# JSON-RPC 2.0 reserved error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP methods this server implements.
INITIALIZE = "initialize"
INITIALIZED = "notifications/initialized"
PING = "ping"
TOOLS_LIST = "tools/list"
TOOLS_CALL = "tools/call"
RESOURCES_LIST = "resources/list"
RESOURCES_TEMPLATES_LIST = "resources/templates/list"
RESOURCES_READ = "resources/read"
PROMPTS_LIST = "prompts/list"
PROMPTS_GET = "prompts/get"

# Streamable HTTP headers. A stateless server never issues SESSION_ID_HEADER —
# it is named here only so the transport can document what it deliberately
# ignores. PROTOCOL_VERSION_HEADER is what replaces the remembered handshake:
# every request states the revision it belongs to.
PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
SESSION_ID_HEADER = "Mcp-Session-Id"

# Revision to assume for a request that carries no PROTOCOL_VERSION_HEADER. The
# spec pins this to 2025-03-26 for backwards compatibility (the header did not
# exist before 2025-06-18), and `initialize` legitimately arrives without it.
ASSUMED_PROTOCOL_VERSION = "2025-03-26"


def negotiate_protocol_version(requested: Any) -> str:
    """Revision to answer an ``initialize`` with.

    Echo what the client asked for when we speak it; otherwise answer with our
    latest and let the client accept or disconnect (spec: version negotiation).
    """

    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return PROTOCOL_VERSION


def client_name_from(message: dict[str, Any]) -> str | None:
    """The client name an ``initialize`` message announces, if any.

    Stateless transports read this per request rather than remembering it from a
    prior handshake, so the audit trail still names the caller.
    """

    if message.get("method") != INITIALIZE:
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    info = params.get("clientInfo")
    if not isinstance(info, dict):
        return None
    name = info.get("name")
    return name.strip() or None if isinstance(name, str) else None


class JsonRpcError(Exception):
    """Raised inside dispatch to produce a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def result_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def is_notification(message: dict[str, Any]) -> bool:
    """A JSON-RPC message with no ``id`` is a notification (no response)."""

    return "id" not in message
