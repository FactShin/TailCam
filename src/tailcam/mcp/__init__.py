"""TailCam MCP integration.

Turns a TailCam node and its fleet into an agent-ready control plane for Codex,
Claude, and local agent systems such as OpenClaw/Hermes. The package exposes a
single transport-agnostic server core (:mod:`tailcam.mcp.server`) plus two
transports: local stdio (:mod:`tailcam.mcp.transport_stdio`) and an in-process
Streamable HTTP mount (:mod:`tailcam.mcp.transport_http`).

The core speaks the Model Context Protocol (revision ``2025-06-18``) over
JSON-RPC 2.0 directly, wrapping TailCam's stable REST and v1 management APIs
rather than bypassing them. See ``docs/mcp.md`` for client setup and
``docs/mcp-security.md`` for the transport, identity, and audit model.
"""

from __future__ import annotations

PROTOCOL_VERSION = "2025-06-18"
# Older revisions this server can also speak (the message set we implement is
# compatible: initialize/tools/resources/prompts with no batching reliance).
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "tailcam"
# The local-stdio invocation agents put in their MCP client config. Single
# source of truth so the CLI, docs, and the MCP page can't advertise different
# commands (matches the `tailcam mcp stdio` CLI subcommand).
STDIO_COMMAND = "tailcam mcp stdio"
STDIO_ARGS = ["mcp", "stdio"]

# The safe starter tool set an agent should auto-enable first: read + incident
# tools, no admin/writes. Single source of truth for both the connect snippets
# the MCP page renders and the examples/mcp/* files (kept in sync by test).
RECOMMENDED_TOOLS = [
    "get_system_status",
    "list_fleet_nodes",
    "list_cameras",
    "inspect_camera",
    "list_recent_events",
    "summarize_fleet_health",
    "find_offline_cameras",
    "investigate_motion_event",
]

__all__ = [
    "PROTOCOL_VERSION",
    "RECOMMENDED_TOOLS",
    "SERVER_NAME",
    "STDIO_ARGS",
    "STDIO_COMMAND",
    "SUPPORTED_PROTOCOL_VERSIONS",
]
