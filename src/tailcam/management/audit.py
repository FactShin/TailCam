"""Audit logging service for TailCam management actions."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from tailcam.persistence.models import AuditRecord
from tailcam.persistence.store import Store


class AuditLog:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record(
        self,
        *,
        actor: str,
        source: str,
        action: str,
        target: str,
        result: str,
        detail: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        created_ts: float | None = None,
    ) -> int:
        return self._store.add_audit_event(
            AuditRecord(
                id=None,
                created_ts=time.time() if created_ts is None else created_ts,
                actor=actor,
                source=source,
                action=action,
                target=target,
                result=result,
                detail=detail,
                metadata_json=_metadata_json(metadata),
            )
        )

    def list(self, limit: int = 100, offset: int = 0) -> list[AuditRecord]:
        return self._store.list_audit_events(limit=limit, offset=offset)


def _metadata_json(metadata: Mapping[str, Any] | None) -> str:
    return json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
