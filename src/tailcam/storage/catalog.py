"""Local SQLite artifact journal and cached fleet index; never a shared database."""

from __future__ import annotations

import builtins
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from tailcam.persistence.store import Store
from tailcam.storage.models import Artifact, StorageError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS storage_locations (
    id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, active INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS storage_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS storage_artifacts (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, origin TEXT NOT NULL, camera TEXT NOT NULL,
    kind TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, data TEXT NOT NULL,
    local_path TEXT, location TEXT, size INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS storage_artifact_filter
ON storage_artifacts(origin,camera,kind,created DESC,id);
CREATE TABLE IF NOT EXISTS storage_aliases (
    namespace TEXT NOT NULL, legacy_id TEXT NOT NULL, variant TEXT NOT NULL,
    artifact_id TEXT NOT NULL, PRIMARY KEY(namespace,legacy_id,variant)
);
CREATE TABLE IF NOT EXISTS storage_copies (
    artifact_id TEXT NOT NULL, location TEXT NOT NULL, path TEXT NOT NULL,
    size INTEGER NOT NULL, PRIMARY KEY(artifact_id,location)
);
CREATE TABLE IF NOT EXISTS storage_changes (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS storage_reservations (
    id TEXT PRIMARY KEY, location TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes>=0),
    category TEXT NOT NULL, path TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS storage_transfers (
    id TEXT PRIMARY KEY, origin TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    artifact_id TEXT NOT NULL, manifest TEXT NOT NULL, data TEXT NOT NULL,
    location TEXT NOT NULL, UNIQUE(origin,idempotency_key), UNIQUE(artifact_id,location)
);
CREATE TABLE IF NOT EXISTS storage_outbound (
    id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE, data TEXT NOT NULL
);
"""


class ArtifactCatalog:
    def __init__(self, store: Store, node_id: str) -> None:
        self.store = store
        self.node_id = node_id
        self.connection.executescript(_SCHEMA)

    @property
    def connection(self) -> sqlite3.Connection:
        return self.store._conn()

    @contextmanager
    def transaction(self):
        conn = self.connection
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def get(self, artifact_id: str) -> Artifact | None:
        row = self.connection.execute(
            "SELECT data FROM storage_artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        return Artifact.model_validate_json(row[0]) if row else None

    def local_path(self, artifact_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT local_path FROM storage_artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        return row[0] if row else None

    def save(
        self,
        artifact: Artifact,
        local_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        publish: bool | None = None,
    ) -> None:
        if connection is None:
            with self.transaction() as conn:
                self.save(artifact, local_path, connection=conn, publish=publish)
            return
        encoded = artifact.model_dump_json()
        connection.execute(
            """INSERT INTO storage_artifacts
               (id,owner,origin,camera,kind,state,created,data,local_path,location,size)
               VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               owner=excluded.owner,origin=excluded.origin,camera=excluded.camera,
               kind=excluded.kind,state=excluded.state,created=excluded.created,
               data=excluded.data,local_path=excluded.local_path,location=excluded.location,
               size=excluded.size""",
            (
                artifact.artifact_id,
                artifact.owner_node_id,
                artifact.origin_node_id,
                artifact.camera_id,
                artifact.kind,
                artifact.state,
                artifact.created_at,
                encoded,
                local_path,
                artifact.location_id,
                artifact.size_bytes,
            ),
        )
        if publish if publish is not None else artifact.owner_node_id == self.node_id:
            connection.execute(
                "INSERT INTO storage_changes(artifact_id,data) VALUES(?,?)",
                (artifact.artifact_id, encoded),
            )
        if local_path and artifact.location_id and artifact.state != "deleted":
            connection.execute(
                "INSERT INTO storage_copies VALUES(?,?,?,?) ON CONFLICT(artifact_id,location) "
                "DO UPDATE SET path=excluded.path,size=excluded.size",
                (artifact.artifact_id, artifact.location_id, local_path, artifact.size_bytes),
            )

    def list(
        self,
        *,
        kind: str | None = None,
        origin_node_id: str | None = None,
        camera_id: str | None = None,
        state: str | None = None,
        owner_node_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Artifact]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise StorageError("invalid_page", "Invalid artifact page.", 422)
        terms, values = [], []
        for field, value in (
            ("kind", kind),
            ("origin", origin_node_id),
            ("camera", camera_id),
            ("state", state),
            ("owner", owner_node_id),
        ):
            if value is not None:
                terms.append(f"{field}=?")
                values.append(value)
        if state is None:
            terms.append("state!='deleted'")
        where = " AND ".join(terms)
        rows = self.connection.execute(
            f"SELECT data FROM storage_artifacts WHERE {where} "
            "ORDER BY created DESC,id LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        return [Artifact.model_validate_json(r[0]) for r in rows]

    def alias(self, namespace: str, legacy_id: str, variant: str, artifact_id: str) -> None:
        if not self.get(artifact_id):
            raise StorageError("artifact_missing", "Artifact does not exist.", 404)
        if any(len(str(value)) > 256 for value in (namespace, legacy_id, variant)):
            raise StorageError("invalid_alias", "Artifact alias is too long.", 422)
        with self.connection:
            self.connection.execute(
                """INSERT INTO storage_aliases VALUES(?,?,?,?)
                   ON CONFLICT(namespace,legacy_id,variant)
                   DO UPDATE SET artifact_id=excluded.artifact_id""",
                (namespace, str(legacy_id), variant, artifact_id),
            )

    def resolve_alias(self, namespace: str, legacy_id: str, variant: str = "") -> Artifact | None:
        row = self.connection.execute(
            "SELECT artifact_id FROM storage_aliases "
            "WHERE namespace=? AND legacy_id=? AND variant=?",
            (namespace, str(legacy_id), variant),
        ).fetchone()
        return self.get(row[0]) if row else None

    def aliases(self, namespace: str, legacy_id: str) -> builtins.list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT namespace,legacy_id,variant,artifact_id FROM storage_aliases "
            "WHERE namespace=? AND legacy_id=? ORDER BY variant",
            (namespace, str(legacy_id)),
        ).fetchall()
        return [dict(row) for row in rows]

    def import_index(
        self,
        owner_node_id: str,
        artifacts: builtins.list[Artifact | dict[str, Any]],
    ) -> int:
        if len(artifacts) > 1000:
            raise StorageError("page_too_large", "Catalog page exceeds 1000 artifacts.", 422)
        validated = [Artifact.model_validate(a) for a in artifacts]
        with self.transaction() as conn:
            for artifact in validated:
                if owner_node_id == self.node_id:
                    raise StorageError("owner_mismatch", "Catalog owner does not match its peer.")
                old = self.get(artifact.artifact_id)
                if old and old.owner_node_id != owner_node_id:
                    raise StorageError(
                        "owner_conflict", "A different peer owns this artifact UUID."
                    )
                if artifact.owner_node_id != owner_node_id and old is None:
                    # An old owner's handoff for an artifact never observed here; the new
                    # owner's own feed supplies the authoritative full inventory.
                    continue
                if old and (old.sha256, old.size_bytes, old.origin_node_id, old.kind) != (
                    artifact.sha256,
                    artifact.size_bytes,
                    artifact.origin_node_id,
                    artifact.kind,
                ):
                    raise StorageError(
                        "artifact_conflict", "Peer changed immutable artifact content."
                    )
                if old and old.updated_at > artifact.updated_at:
                    continue
                artifact = artifact.model_copy(
                    update={"owner_online": True, "last_seen": time.time()}
                )
                self.save(artifact, connection=conn, publish=False)
        return len(validated)

    def mark_owner_offline(self, owner_node_id: str) -> None:
        rows = self.connection.execute(
            "SELECT data FROM storage_artifacts WHERE owner=?", (owner_node_id,)
        ).fetchall()
        with self.transaction() as conn:
            for row in rows:
                artifact = Artifact.model_validate_json(row[0])
                artifact.owner_online = False
                self.save(artifact, connection=conn, publish=False)

    def changes(self, after: int = 0, limit: int = 100) -> dict[str, Any]:
        if after < 0 or not 1 <= limit <= 1000:
            raise StorageError("invalid_page", "Invalid catalog cursor.", 422)
        rows = self.connection.execute(
            "SELECT sequence,data FROM storage_changes WHERE sequence>? ORDER BY sequence LIMIT ?",
            (after, limit),
        ).fetchall()
        return {
            "cursor": rows[-1][0] if rows else after,
            "artifacts": [json.loads(r[1]) for r in rows],
        }

    def mark_owner_online(self, owner_node_id: str) -> None:
        rows = self.connection.execute(
            "SELECT data FROM storage_artifacts WHERE owner=?",
            (owner_node_id,),
        ).fetchall()
        with self.transaction() as conn:
            for row in rows:
                artifact = Artifact.model_validate_json(row[0])
                artifact.owner_online, artifact.last_seen = True, time.time()
                self.save(artifact, connection=conn, publish=False)

    def setting(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM storage_settings WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str, *, connection=None) -> None:
        conn = connection or self.connection
        conn.execute(
            "INSERT INTO storage_settings VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        if connection is None:
            conn.commit()
