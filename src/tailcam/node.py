"""Node workload roles, independent of a caller's security permissions."""

from __future__ import annotations

ROLE_NAMES = ("capture", "storage", "analysis", "training")
ROLE_PRESETS: dict[str, tuple[str, ...]] = {
    "hub": (),
    "camera": ("capture", "storage"),
    "storage": ("storage",),
    "compute": ("analysis", "training"),
    "all-in-one": ROLE_NAMES,
}


class NodeConfigError(ValueError):
    """Invalid workload configuration must never enable default workloads."""


class RoleDisabledError(RuntimeError):
    def __init__(self, role: str) -> None:
        self.role = role
        super().__init__(f"The {role} role is disabled on this node; configure roles and restart.")


def validate_roles(value: object) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(role, str) or role not in ROLE_NAMES for role in value
    ):
        raise NodeConfigError(
            "node.roles must be a list containing capture, storage, analysis, or training; "
            "use [] for a hub"
        )
    if len(value) != len(set(value)):
        raise NodeConfigError("node.roles must not contain duplicate roles")
    return [role for role in ROLE_NAMES if role in value]


def validate_node_name(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise NodeConfigError("node.name must be a string of at most 64 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise NodeConfigError("node.name must not contain control characters")
    return value.strip()
