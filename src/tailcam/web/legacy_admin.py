"""Administrative boundaries shared by legacy REST routes and the peer proxy."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import Depends

from tailcam.security.principal import RequestPrincipal
from tailcam.web.routes_node_v1 import get_principal, require_admin
from tailcam.web.schemas import StorageUpdate

_STORAGE_ADMIN_FIELDS = frozenset({
    "media_dir", "node", "retention_enabled", "max_gb", "max_age_days",
})
_SECRET_READS = frozenset({"api/notifications", "api/integrations"})
_ADMIN_WRITES = frozenset({"api/ai", "api/ai/pull", "api/ai/load", "api/mcp"})
_ADMIN_WRITE_FAMILIES = frozenset({
    "plugins", "notifications", "integrations", "training", "models", "datasets",
    "samples", "active-learning",
})


def storage_admin_change(update: Mapping[str, object]) -> bool:
    # Null means unchanged in the legacy schema. False, zero and empty strings
    # are changes too (including resetting a path or disabling retention).
    return any(update.get(field) is not None for field in _STORAGE_ADMIN_FIELDS)


def require_storage_admin(
    update: StorageUpdate,
    principal: RequestPrincipal = Depends(get_principal),
) -> None:
    """Authorize the whole body before applying any mixed storage/motion edit."""
    if storage_admin_change(update.model_dump()):
        require_admin(principal)


def legacy_admin_request(
    method: str, path: str, *, storage_update: Mapping[str, object] | None = None,
) -> bool:
    """Whether a legacy request must reach the destination directly.

    The generic proxy strips caller identity. Even an administrator must use
    the destination's own authenticated endpoint for administrative actions.
    Camera paths and motion-only storage edits retain their existing behavior.
    """
    normalized = "/".join(part for part in path.split("/") if part)
    if method in {"GET", "HEAD"}:
        return normalized in _SECRET_READS
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if normalized == "api/storage":
        return storage_update is None or storage_admin_change(storage_update)
    parts = normalized.split("/")
    return normalized in _ADMIN_WRITES or (
        len(parts) >= 2 and parts[0] == "api" and parts[1] in _ADMIN_WRITE_FAMILIES
    )
