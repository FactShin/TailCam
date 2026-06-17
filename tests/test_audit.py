from __future__ import annotations

import json

from tailcam.management.audit import AuditLog
from tailcam.persistence.models import AuditRecord


def test_audit_log_records_metadata_and_lists_newest_first(store) -> None:
    audit = AuditLog(store)

    first_id = audit.record(
        actor="alice@example.com",
        source="tailscale-user",
        action="node.reload",
        target="office-mac",
        result="success",
        detail="capture workers reloaded",
        metadata={"camera_count": 2},
        created_ts=123.0,
    )
    second_id = audit.record(
        actor="local",
        source="local",
        action="node.reload",
        target="workbench",
        result="failure",
        detail="camera discovery failed",
        metadata={"error": "boom"},
        created_ts=124.0,
    )

    records = audit.list(limit=10)

    assert [record.id for record in records] == [second_id, first_id]
    assert records[1] == AuditRecord(
        id=first_id,
        created_ts=123.0,
        actor="alice@example.com",
        source="tailscale-user",
        action="node.reload",
        target="office-mac",
        result="success",
        detail="capture workers reloaded",
        metadata_json='{"camera_count":2}',
    )
    assert json.loads(records[0].metadata_json) == {"error": "boom"}


def test_audit_log_limit_and_offset(store) -> None:
    audit = AuditLog(store)
    ids = [
        audit.record(
            actor="local",
            source="local",
            action=f"node.action.{idx}",
            target="node-a",
            result="success",
            created_ts=float(idx),
        )
        for idx in range(3)
    ]

    assert [record.id for record in audit.list(limit=1, offset=0)] == [ids[2]]
    assert [record.id for record in audit.list(limit=1, offset=1)] == [ids[1]]
    assert [record.id for record in audit.list(limit=5, offset=2)] == [ids[0]]


def test_audit_schema_advances_store_version(store) -> None:
    row = store._conn().execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    tables = {
        record["name"]
        for record in store._conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    assert row["version"] == 9
    assert "audit_events" in tables
