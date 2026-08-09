"""Local stdio transport: line-delimited JSON-RPC on stdin/stdout.

This is the mode local Codex, Claude Desktop, and Hermes/OpenClaw agents launch
via ``tailcam mcp stdio``. It connects to a running TailCam node over HTTP
(``TAILCAM_URL`` or ``http://127.0.0.1:8088``) and follows TailCam's
personal-computer trust model: whoever can launch the process acts as the local
admin. The node still audits privileged v1 actions on its own side.

The server core is stateless, so one is built per message. stdio *is* a single
long-lived connection, though, so the one genuinely connection-scoped fact —
the client name the peer announced at ``initialize`` — is remembered here, at
the transport, rather than inside the core.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from tailcam.config import AppConfig
from tailcam.mcp.client import DEFAULT_URL, TailcamClient
from tailcam.mcp.protocol import PARSE_ERROR, client_name_from, error_response
from tailcam.mcp.server import McpServer
from tailcam.security.principal import RequestPrincipal, TailCamRole

# Builds the server for one message, given the client name seen so far.
ServerFactory = Callable[[str | None], McpServer]

_LOCAL_PRINCIPAL = RequestPrincipal(
    actor="local",
    display_name="Local TailCam (stdio)",
    source="local",
    verified=True,
    roles=frozenset({TailCamRole.VIEWER, TailCamRole.OPERATOR, TailCamRole.ADMIN}),
)


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, default=str) + "\n")
    sys.stdout.flush()


async def serve_stdio(new_server: ServerFactory) -> None:
    """Read JSON-RPC lines from stdin and write responses until EOF."""

    loop = asyncio.get_event_loop()
    client_name: str | None = None
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:  # EOF
            break
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(error_response(None, PARSE_ERROR, "parse error"))
            continue
        if not isinstance(message, dict):
            _write(error_response(None, PARSE_ERROR, "expected a JSON-RPC object"))
            continue
        client_name = client_name_from(message) or client_name
        response = await new_server(client_name).handle(message)
        if response is not None:
            _write(response)


def run_stdio(config: AppConfig) -> None:
    """Blocking entry point for ``tailcam mcp stdio``."""

    base_url = os.environ.get("TAILCAM_URL") or DEFAULT_URL

    async def _main() -> None:
        client = TailcamClient.for_url(base_url)

        def new_server(client_name: str | None) -> McpServer:
            return McpServer(
                client=client,
                principal=_LOCAL_PRINCIPAL,
                config=config.mcp,
                transport="stdio",
                client_name=client_name,
            )

        try:
            await serve_stdio(new_server)
        finally:
            await client.aclose()

    asyncio.run(_main())
