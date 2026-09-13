"""Pure policy selection; no network, filesystem, model or camera work."""

from __future__ import annotations

from tailcam.storage.models import ContentKind, DestinationRef, StoragePolicy


def destination_for(
    policy: StoragePolicy,
    kind: ContentKind,
    origin_node_id: str,
    camera_id: str,
) -> DestinationRef:
    matches = [
        rule
        for rule in policy.overrides
        if (rule.origin_node_id is None or rule.origin_node_id == origin_node_id)
        and (rule.camera_id is None or rule.camera_id == camera_id)
        and (rule.content_kind is None or rule.content_kind == kind)
    ]
    if not matches:
        return policy.default_destination.model_copy(deep=True)
    # Camera+kind > camera > kind. Origin-scoped kinds beat a fleet-wide kind.
    selected = max(
        matches,
        key=lambda r: (2 * bool(r.camera_id) + bool(r.content_kind), bool(r.origin_node_id)),
    )
    return selected.destination.model_copy(deep=True)
