"""Capability discovery for TailCam node-management APIs."""

from __future__ import annotations

from dataclasses import dataclass

from tailcam.security.principal import RequestPrincipal


@dataclass(frozen=True)
class NodeCapabilitySet:
    api_version: str
    capabilities: frozenset[str]
    actions: frozenset[str]
    principal_verified: bool = False
    principal_roles: frozenset[str] = frozenset()


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
    def snapshot(self, principal: RequestPrincipal | None = None) -> NodeCapabilitySet:
        return NodeCapabilitySet(
            api_version="1",
            capabilities=_CAPABILITIES,
            actions=_ACTIONS,
            principal_verified=bool(principal and principal.verified),
            principal_roles=(
                frozenset(role.value for role in principal.roles) if principal else frozenset()
            ),
        )
