"""JSON-RPC 2.0 plumbing for the MCP server core.

The MCP wire protocol is line-delimited JSON-RPC 2.0 over stdio and POSTed
JSON-RPC over Streamable HTTP. This module owns the small set of framing helpers
and the protocol method names so the rest of the package never hand-builds
envelopes.
"""

from __future__ import annotations

from typing import Any

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
