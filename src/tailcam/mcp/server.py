"""Transport-agnostic MCP server core.

One :class:`McpServer` represents a single connection (a stdio process or one
HTTP request). It owns the protocol handshake, the tool/resource/prompt
registries, role-based authorization, and result shaping. Transports only feed it
parsed JSON-RPC messages and a resolved principal.
"""

from __future__ import annotations

from typing import Any

from tailcam import __version__
from tailcam.config import MCPConfig
from tailcam.management.audit import AuditLog
from tailcam.mcp import PROTOCOL_VERSION, SERVER_NAME, prompts, resources
from tailcam.mcp.client import TailcamClient
from tailcam.mcp.errors import TailcamMcpError, error_envelope
from tailcam.mcp.protocol import (
    INITIALIZE,
    INITIALIZED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PING,
    PROMPTS_GET,
    PROMPTS_LIST,
    RESOURCES_LIST,
    RESOURCES_READ,
    RESOURCES_TEMPLATES_LIST,
    TOOLS_CALL,
    TOOLS_LIST,
    JsonRpcError,
    error_response,
    is_notification,
    result_response,
)
from tailcam.mcp.toolctx import ToolContext, principal_rank
from tailcam.mcp.tools import Tool, ToolResult, build_tools
from tailcam.security.principal import RequestPrincipal, TailCamRole

RESOURCE_NOT_FOUND = -32002

_ROLE_RANK = {TailCamRole.VIEWER: 1, TailCamRole.OPERATOR: 2, TailCamRole.ADMIN: 3}

INSTRUCTIONS = (
    "TailCam controls cameras over Tailscale. Prefer read tools before write tools. "
    "Use resources for status/context, tools for actions, and prompts for workflows. "
    "Never attempt arbitrary proxying. Fleet or destructive actions require explicit "
    "confirmation strings and are audited. Summarize risks before changing recording, "
    "motion, AI, retention, or fleet state.\n\n"
    "Triage: read tailcam://fleet, then summarize_fleet_health and find_offline_cameras. "
    "Investigation: investigate_motion_event with an event id, then prepare_incident_report. "
    "Safe admin: prepare_fleet_admin_plan first, then reload_node/reload_fleet_nodes with the "
    "exact confirm_scope it returns. Snapshots are safe; restart_camera, AI, and fleet writes "
    "need confirmation. Read tools require a verified principal; admin tools need the admin role."
)


class McpServer:
    def __init__(
        self,
        *,
        client: TailcamClient,
        principal: RequestPrincipal,
        config: MCPConfig,
        transport: str,
        audit: AuditLog | None = None,
    ) -> None:
        self.client = client
        self.principal = principal
        self.config = config
        self.transport = transport
        self.audit = audit
        self.client_name: str | None = None
        self._tools: dict[str, Tool] = {t.name: t for t in build_tools()}

    # -- public dispatch ---------------------------------------------------
    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC message; return a response (or None for notifications)."""

        notif = is_notification(message)
        method = message.get("method")
        if not isinstance(method, str):
            return None if notif else error_response(
                message.get("id"), INVALID_REQUEST, "missing or invalid 'method'"
            )
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            result = await self._dispatch(method, params)
        except JsonRpcError as exc:
            return None if notif else error_response(
                message.get("id"), exc.code, exc.message, exc.data
            )
        except Exception as exc:  # pragma: no cover - safety net
            return None if notif else error_response(
                message.get("id"), INTERNAL_ERROR, f"internal error: {exc}"
            )
        return None if notif else result_response(message.get("id"), result)

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == INITIALIZE:
            return self._initialize(params)
        if method == INITIALIZED:
            return {}
        if method == PING:
            return {}
        if method == TOOLS_LIST:
            return {"tools": [self._tool_def(t) for t in self._visible_tools()]}
        if method == TOOLS_CALL:
            return await self._call_tool(params)
        if method == RESOURCES_LIST:
            return {"resources": [
                {"uri": r.uri, "name": r.name, "description": r.description,
                 "mimeType": "application/json"}
                for r in resources.STATIC
            ]}
        if method == RESOURCES_TEMPLATES_LIST:
            return {"resourceTemplates": [
                {"uriTemplate": t.uri_template, "name": t.name, "description": t.description,
                 "mimeType": "application/json"}
                for t in resources.TEMPLATES
            ]}
        if method == RESOURCES_READ:
            return await self._read_resource(params)
        if method == PROMPTS_LIST:
            return {"prompts": [
                {"name": p.name, "description": p.description, "arguments": p.arguments}
                for p in prompts.PROMPTS
            ]}
        if method == PROMPTS_GET:
            return self._get_prompt(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    # -- handlers ----------------------------------------------------------
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        client_info = params.get("clientInfo")
        if isinstance(client_info, dict):
            name = client_info.get("name")
            if isinstance(name, str):
                self.client_name = name
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "title": "TailCam", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    async def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise JsonRpcError(INVALID_PARAMS, "tools/call requires a string 'name'")
        tool = self._tools.get(name)
        if tool is None:
            raise JsonRpcError(INVALID_PARAMS, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(INVALID_PARAMS, "'arguments' must be an object")
        if not self._authorized(tool):
            return self._tool_error(_authz_error(tool, self.principal))
        ctx = self._tool_context()
        try:
            result = await tool.handler(ctx, arguments)
        except TailcamMcpError as exc:
            return self._tool_error(exc)
        return self._tool_ok(result)

    async def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise JsonRpcError(INVALID_PARAMS, "resources/read requires a string 'uri'")
        try:
            return await resources.read_resource(self._tool_context(), uri)
        except TailcamMcpError as exc:
            code = RESOURCE_NOT_FOUND if exc.status_code == 404 else INTERNAL_ERROR
            raise JsonRpcError(code, exc.message, exc.to_payload()) from exc

    def _get_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise JsonRpcError(INVALID_PARAMS, "prompts/get requires a string 'name'")
        try:
            return prompts.render(name, params.get("arguments"))
        except KeyError as exc:
            raise JsonRpcError(INVALID_PARAMS, f"unknown prompt: {name}") from exc

    # -- helpers -----------------------------------------------------------
    def _tool_context(self) -> ToolContext:
        return ToolContext(
            client=self.client,
            principal=self.principal,
            config=self.config,
            transport=self.transport,
            client_name=self.client_name,
            audit=self.audit,
        )

    def _visible_tools(self) -> list[Tool]:
        return [t for t in self._tools.values() if self._authorized(t)]

    def _authorized(self, tool: Tool) -> bool:
        if not self.principal.verified:
            return False
        return principal_rank(self.principal) >= _ROLE_RANK[tool.min_role]

    def _tool_def(self, tool: Tool) -> dict[str, Any]:
        return {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "inputSchema": tool.input_schema,
            "annotations": {"title": tool.title, "readOnlyHint": not tool.write},
        }

    def _tool_ok(self, result: ToolResult) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": result.summary}],
            "structuredContent": result.data,
            "isError": False,
        }

    def _tool_error(self, error: TailcamMcpError) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": f"Error [{error.code}]: {error.message}"}],
            "structuredContent": error_envelope(error),
            "isError": True,
        }


def _authz_error(tool: Tool, principal: RequestPrincipal) -> TailcamMcpError:
    from tailcam.mcp import errors

    if not principal.verified:
        return TailcamMcpError(errors.UNAUTHORIZED, "a verified principal is required")
    if tool.min_role == TailCamRole.ADMIN:
        return TailcamMcpError(errors.ADMIN_REQUIRED, f"'{tool.name}' requires the admin role")
    return TailcamMcpError(
        errors.UNAUTHORIZED, f"'{tool.name}' requires the {tool.min_role.value} role"
    )
