"""Per-call context handed to every MCP tool, resource, and prompt handler.

Bundles the TailCam client, the resolved request principal, transport metadata,
and an audit helper. Keeping this in one object means handlers stay small and the
audit trail is uniform: every state-changing tool records who did what, over
which transport, with which client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tailcam.config import MCPConfig
from tailcam.management.audit import AuditLog
from tailcam.mcp.client import TailcamClient
from tailcam.security.principal import RequestPrincipal, TailCamRole

_ROLE_RANK = {TailCamRole.VIEWER: 1, TailCamRole.OPERATOR: 2, TailCamRole.ADMIN: 3}


def principal_rank(principal: RequestPrincipal) -> int:
    """Highest role rank a principal holds (0 if none/unverified)."""

    if not principal.verified:
        return 0
    return max((_ROLE_RANK.get(role, 0) for role in principal.roles), default=0)


@dataclass
class ToolContext:
    client: TailcamClient
    principal: RequestPrincipal
    config: MCPConfig
    transport: str  # "stdio" | "streamable_http"
    client_name: str | None = None
    audit: AuditLog | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def record_action(
        self,
        *,
        action: str,
        target: str,
        result: str,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort audit of a state-changing tool.

        No-ops when the server has no local store (stdio against a remote node):
        in that case the node's own v1 endpoints still record privileged actions.
        """

        if self.audit is None:
            return
        meta: dict[str, Any] = {
            "mcp_transport": self.transport,
            "mcp_tool": action,
        }
        if self.client_name:
            meta["mcp_client"] = self.client_name
        if metadata:
            meta.update(metadata)
        self.audit.record(
            actor=self.principal.actor,
            source=self.principal.source,
            action=f"mcp.{action}",
            target=target,
            result=result,
            detail=detail,
            metadata=meta,
        )
