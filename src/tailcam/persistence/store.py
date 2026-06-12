"""SQLite store with a tiny migration system. WAL mode for concurrent writes."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from tailcam import paths
from tailcam.persistence.models import CameraRecord, MediaRecord, MotionEventRecord

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
    """,
    """
    CREATE TABLE IF NOT EXISTS cameras (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        backend TEXT NOT NULL,
        settings_json TEXT NOT NULL,
        last_seen REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        camera_id TEXT NOT NULL,
        media_type TEXT NOT NULL,
        path TEXT NOT NULL,
        thumbnail TEXT,
        created_ts REAL NOT NULL,
        trigger TEXT NOT NULL DEFAULT 'manual',
        size_bytes INTEGER NOT NULL DEFAULT 0
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_media_camera_ts ON media (camera_id, created_ts DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS motion_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        camera_id TEXT NOT NULL,
        start_ts REAL NOT NULL,
        end_ts REAL,
        peak_score REAL NOT NULL DEFAULT 0,
        recording_id INTEGER,
        label TEXT,
        description TEXT,
        confidence REAL,
        thumb_path TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_camera_ts ON motion_events (camera_id, start_ts DESC);
    """,
]
_CURRENT_VERSION = 2

# Columns added after v1 — applied to existing DBs via ALTER TABLE on migrate().
_EVENT_COLUMNS = {
    "label": "TEXT",
    "description": "TEXT",
    "confidence": "REAL",
    "thumb_path": "TEXT",
}


class Store:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or paths.database_file()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.migrate()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return conn

    def migrate(self) -> None:
        conn = self._conn()
        with conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            # Add columns introduced after v1 to a pre-existing motion_events table.
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(motion_events)")}
            for col, col_type in _EVENT_COLUMNS.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE motion_events ADD COLUMN {col} {col_type}")
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (_CURRENT_VERSION,))
            else:
                conn.execute("UPDATE schema_version SET version=?", (_CURRENT_VERSION,))

    # -- cameras -----------------------------------------------------------
    def upsert_camera(self, record: CameraRecord) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO cameras (id, name, backend, settings_json, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    backend=excluded.backend,
                    settings_json=excluded.settings_json,
                    last_seen=excluded.last_seen
                """,
                (record.id, record.name, record.backend, record.settings_json, record.last_seen),
            )

    def get_camera(self, camera_id: str) -> CameraRecord | None:
        row = self._conn().execute("SELECT * FROM cameras WHERE id=?", (camera_id,)).fetchone()
        return _camera_from_row(row) if row else None

    def list_cameras(self) -> list[CameraRecord]:
        rows = self._conn().execute("SELECT * FROM cameras ORDER BY name").fetchall()
        return [_camera_from_row(r) for r in rows]

    def set_camera_name(self, camera_id: str, name: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE cameras SET name=? WHERE id=?", (name, camera_id))

    def delete_camera(self, camera_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM cameras WHERE id=?", (camera_id,))

    def set_camera_settings(self, camera_id: str, settings: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE cameras SET settings_json=? WHERE id=?",
                (json.dumps(settings), camera_id),
            )

    # -- media -------------------------------------------------------------
    def add_media(self, record: MediaRecord) -> int:
        conn = self._conn()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO media
                    (camera_id, media_type, path, thumbnail, created_ts, trigger, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.camera_id,
                    record.media_type,
                    record.path,
                    record.thumbnail,
                    record.created_ts,
                    record.trigger,
                    record.size_bytes,
                ),
            )
            return int(cur.lastrowid or 0)

    def list_media(
        self,
        camera_id: str | None = None,
        media_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MediaRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if camera_id:
            clauses.append("camera_id=?")
            params.append(camera_id)
        if media_type:
            clauses.append("media_type=?")
            params.append(media_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._conn().execute(
            f"SELECT * FROM media {where} ORDER BY created_ts DESC LIMIT ? OFFSET ?", params
        ).fetchall()
        return [_media_from_row(r) for r in rows]

    def get_media(self, media_id: int) -> MediaRecord | None:
        row = self._conn().execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        return _media_from_row(row) if row else None

    def delete_media(self, media_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM media WHERE id=?", (media_id,))

    def total_media_bytes(self) -> int:
        row = self._conn().execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM media"
        ).fetchone()
        return int(row["total"])

    # -- motion events -----------------------------------------------------
    def add_motion_event(self, record: MotionEventRecord) -> int:
        conn = self._conn()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO motion_events (camera_id, start_ts, end_ts, peak_score, recording_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (record.camera_id, record.start_ts, record.end_ts, record.peak_score,
                 record.recording_id),
            )
            return int(cur.lastrowid or 0)

    def update_motion_event(
        self, event_id: int, end_ts: float, peak_score: float, recording_id: int | None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE motion_events SET end_ts=?, peak_score=?, recording_id=? WHERE id=?",
                (end_ts, peak_score, recording_id, event_id),
            )

    def set_event_thumb(self, event_id: int, thumb_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE motion_events SET thumb_path=? WHERE id=?", (thumb_path, event_id)
            )

    def set_event_analysis(
        self, event_id: int, label: str, description: str | None, confidence: float | None
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE motion_events SET label=?, description=?, confidence=? WHERE id=?",
                (label, description, confidence, event_id),
            )

    def get_motion_event(self, event_id: int) -> MotionEventRecord | None:
        row = self._conn().execute(
            "SELECT * FROM motion_events WHERE id=?", (event_id,)
        ).fetchone()
        return _event_from_row(row) if row else None

    def close_stale_motion_events(self) -> int:
        """Close any event still marked ongoing (end_ts IS NULL).

        Called at startup: nothing can genuinely be ongoing then, so leftovers
        are orphans from a crash or an older version that leaked them. The true
        end time is unknown — close at start_ts (zero duration) rather than
        showing a bogus hours-long event.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE motion_events SET end_ts=start_ts WHERE end_ts IS NULL"
            )
            return cur.rowcount

    def list_motion_events(
        self, camera_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[MotionEventRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if camera_id:
            clauses.append("camera_id=?")
            params.append(camera_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._conn().execute(
            f"SELECT * FROM motion_events {where} ORDER BY start_ts DESC LIMIT ? OFFSET ?", params
        ).fetchall()
        return [_event_from_row(r) for r in rows]


def _camera_from_row(row: sqlite3.Row) -> CameraRecord:
    return CameraRecord(
        id=row["id"],
        name=row["name"],
        backend=row["backend"],
        settings_json=row["settings_json"],
        last_seen=row["last_seen"],
    )


def _media_from_row(row: sqlite3.Row) -> MediaRecord:
    return MediaRecord(
        id=row["id"],
        camera_id=row["camera_id"],
        media_type=row["media_type"],
        path=row["path"],
        thumbnail=row["thumbnail"],
        created_ts=row["created_ts"],
        trigger=row["trigger"],
        size_bytes=row["size_bytes"],
    )


def _event_from_row(row: sqlite3.Row) -> MotionEventRecord:
    return MotionEventRecord(
        id=row["id"],
        camera_id=row["camera_id"],
        start_ts=row["start_ts"],
        end_ts=row["end_ts"],
        peak_score=row["peak_score"],
        recording_id=row["recording_id"],
        label=row["label"],
        description=row["description"],
        confidence=row["confidence"],
        thumb_path=row["thumb_path"],
    )


def now() -> float:
    return time.time()
