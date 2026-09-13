"""Capability discovery for TailCam node-management APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tailcam.management.readiness import ReadinessSnapshot
from tailcam.node import ROLE_NAMES
from tailcam.security.principal import RequestPrincipal


@dataclass(frozen=True)
class NodeCapabilitySet:
    api_version: str
    capabilities: frozenset[str]
    actions: frozenset[str]
    principal_verified: bool = False
    principal_roles: frozenset[str] = frozenset()
    node_id: str | None = None
    node_name: str | None = None
    node_roles: tuple[str, ...] | None = None
    readiness: ReadinessSnapshot | None = None


_CAPABILITIES = frozenset(
    {
        "camera.view",
        "camera.control",
        "camera.record",
        "node.health",
        "node.reload",
        "node.audit",
        "ai.ollama.status",
    }
)
_ACTIONS = frozenset({"reload"})


class NodeCapabilityService:
    def __init__(self, context: Any = None) -> None:
        self._context = context

    def snapshot(
        self, principal: RequestPrincipal | None = None, *, probe: bool = False,
    ) -> NodeCapabilitySet:
        ctx = self._context
        roles = frozenset(ROLE_NAMES) if ctx is None else ctx.active_roles
        available = set(_CAPABILITIES)
        if "capture" not in roles:
            available.difference_update({"camera.view", "camera.control", "camera.record"})
        elif "storage" not in roles and not (ctx and ctx.config.storage.node):
            available.discard("camera.record")
        if "analysis" not in roles:
            available.discard("ai.ollama.status")
        return NodeCapabilitySet(
            api_version="1",
            capabilities=frozenset(available),
            actions=_ACTIONS,
            principal_verified=bool(principal and principal.verified),
            principal_roles=(
                frozenset(role.value for role in principal.roles) if principal else frozenset()
            ),
            node_id=ctx.node_id if ctx is not None else None,
            node_name=ctx.config.node.name if ctx is not None else None,
            node_roles=tuple(role for role in ROLE_NAMES if role in roles),
            readiness=ctx.readiness.snapshot(probe=probe) if ctx is not None else None,
        )
