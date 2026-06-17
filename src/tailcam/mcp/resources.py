"""MCP resources: side-effect-free, context-optimized views of TailCam state.

Static resources (``tailcam://system``, ``tailcam://fleet`` …) plus three
templated ones for per-node and per-camera detail. ``resources/read`` returns a
single ``application/json`` text block per URI so agents can pull context cheaply
without invoking tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from tailcam.mcp import errors
from tailcam.mcp.errors import TailcamMcpError
from tailcam.mcp.toolctx import ToolContext, principal_rank
from tailcam.security.principal import TailCamRole

_MIME = "application/json"


@dataclass
class Resource:
    uri: str
    name: str
    description: str


@dataclass
class ResourceTemplate:
    uri_template: str
    name: str
    description: str


STATIC = [
    Resource("tailcam://system", "System", "Local node version, host, Tailscale state, media."),
    Resource("tailcam://fleet", "Fleet", "Local + peer nodes with status, versions, cameras."),
    Resource("tailcam://cameras", "Cameras", "Aggregated camera list across the fleet."),
    Resource("tailcam://events/recent", "Recent events", "Recent motion events with labels."),
    Resource("tailcam://media/recent", "Recent media", "Recent snapshots and recordings."),
    Resource("tailcam://audit/recent", "Recent audit", "Recent audit events (admin only)."),
    Resource("tailcam://ai/status", "AI status", "Ollama/model/training/collection status."),
]

TEMPLATES = [
    ResourceTemplate("tailcam://nodes/{node_key}/health", "Node health", "v1 health for a node."),
    ResourceTemplate(
        "tailcam://nodes/{node_key}/capabilities", "Node capabilities",
        "v1 capabilities and caller principal for a node.",
    ),
    ResourceTemplate("tailcam://cameras/{camera_id}", "Camera", "One camera's full state."),
]

_NODE_HEALTH = re.compile(r"^tailcam://nodes/(?P<key>[^/]+)/health$")
_NODE_CAPS = re.compile(r"^tailcam://nodes/(?P<key>[^/]+)/capabilities$")
_CAMERA = re.compile(r"^tailcam://cameras/(?P<id>.+)$")


async def read_resource(ctx: ToolContext, uri: str) -> dict[str, Any]:
    """Return MCP ``resources/read`` payload for a URI."""

    data = await _resolve(ctx, uri)
    return {
        "contents": [
            {"uri": uri, "mimeType": _MIME, "text": json.dumps(data, indent=2, default=str)}
        ]
    }


async def _resolve(ctx: ToolContext, uri: str) -> Any:
    if uri == "tailcam://system":
        return await ctx.client.system()
    if uri == "tailcam://fleet":
        return {"nodes": await ctx.client.hosts()}
    if uri == "tailcam://cameras":
        return {"cameras": await ctx.client.cameras(scope="all")}
    if uri == "tailcam://events/recent":
        return {"events": await ctx.client.events(limit=ctx.config.max_events)}
    if uri == "tailcam://media/recent":
        return {"media": await ctx.client.media(limit=ctx.config.max_media)}
    if uri == "tailcam://ai/status":
        return {"ai": await ctx.client.ai(), "training": await ctx.client.training()}
    if uri == "tailcam://audit/recent":
        if principal_rank(ctx.principal) < 3 or TailCamRole.ADMIN not in ctx.principal.roles:
            raise TailcamMcpError(errors.ADMIN_REQUIRED, "audit resource requires admin role")
        return {"audit": await ctx.client.node_audit("local", limit=ctx.config.max_events)}

    if match := _NODE_HEALTH.match(uri):
        return await ctx.client.node_health(match.group("key"))
    if match := _NODE_CAPS.match(uri):
        return await ctx.client.node_capabilities(match.group("key"))
    if match := _CAMERA.match(uri):
        return await ctx.client.camera(match.group("id"))

    raise TailcamMcpError(errors.INVALID_REQUEST, f"Unknown resource: {uri}", status_code=404)
