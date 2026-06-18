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
SERVER_NAME = "tailcam"

__all__ = ["PROTOCOL_VERSION", "SERVER_NAME"]
