"""SQLite store with a tiny migration system. WAL mode for concurrent writes."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from tailcam import paths
from tailcam.persistence.models import (
    CameraRecord,
    DatasetRecord,
    DatasetSampleRecord,
    MediaRecord,
    ModelRecord,
    MotionEventRecord,
    TimelapseRecord,
    TrainingRunRecord,
)

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
    """
    CREATE TABLE IF NOT EXISTS timelapses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        camera_id TEXT NOT NULL,
        name TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'capturing',
        mode TEXT NOT NULL DEFAULT 'interval',
        interval_seconds REAL NOT NULL,
        output_fps INTEGER NOT NULL,
        frames_captured INTEGER NOT NULL DEFAULT 0,
        created_ts REAL NOT NULL,
        start_ts REAL NOT NULL,
        end_ts REAL,
        frames_dir TEXT NOT NULL,
        video_path TEXT,
        thumb_path TEXT,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        smooth_state TEXT NOT NULL DEFAULT 'none',
        smooth_path TEXT,
        smooth_size_bytes INTEGER NOT NULL DEFAULT 0,
        smooth_engine TEXT NOT NULL DEFAULT ''
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_timelapses_camera_ts
        ON timelapses (camera_id, created_ts DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS datasets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        task TEXT NOT NULL DEFAULT 'classification',
        created_ts REAL NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS dataset_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id INTEGER NOT NULL,
        path TEXT NOT NULL,
        thumb TEXT,
        label TEXT,
        source TEXT NOT NULL DEFAULT 'collect',
        camera_id TEXT NOT NULL DEFAULT '',
        host TEXT NOT NULL DEFAULT '',
        created_ts REAL NOT NULL,
        confidence REAL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_samples_dataset ON dataset_samples (dataset_id, created_ts DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'trained',
        path TEXT NOT NULL DEFAULT '',
        classes_json TEXT NOT NULL DEFAULT '[]',
        base_model TEXT NOT NULL DEFAULT '',
        metrics_json TEXT NOT NULL DEFAULT '{}',
        created_ts REAL NOT NULL,
        active INTEGER NOT NULL DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS training_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id INTEGER NOT NULL,
        model_id INTEGER,
        base_model TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        params_json TEXT NOT NULL DEFAULT '{}',
        metrics_json TEXT NOT NULL DEFAULT '{}',
        log TEXT NOT NULL DEFAULT '',
        epochs INTEGER NOT NULL DEFAULT 0,
        epoch INTEGER NOT NULL DEFAULT 0,
        created_ts REAL NOT NULL,
        started_ts REAL,
        ended_ts REAL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runs_created ON training_runs (created_ts DESC);
    """,
]
_CURRENT_VERSION = 6

# Columns added after v1 — applied to existing DBs via ALTER TABLE on migrate().
_EVENT_COLUMNS = {
    "label": "TEXT",
    "description": "TEXT",
    "confidence": "REAL",
    "thumb_path": "TEXT",
}

# Smoothing columns added after the v3 timelapses table (applied to older DBs).
_TIMELAPSE_COLUMNS = {
    "smooth_state": "TEXT NOT NULL DEFAULT 'none'",
    "smooth_path": "TEXT",
    "smooth_size_bytes": "INTEGER NOT NULL DEFAULT 0",
    "smooth_engine": "TEXT NOT NULL DEFAULT ''",
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
            # Smoothing columns added to a pre-existing (v3) timelapses table.
            tl_cols = {r["name"] for r in conn.execute("PRAGMA table_info(timelapses)")}
            for col, col_type in _TIMELAPSE_COLUMNS.items():
                if col not in tl_cols:
                    conn.execute(f"ALTER TABLE timelapses ADD COLUMN {col} {col_type}")
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

    # -- timelapses --------------------------------------------------------
    def add_timelapse(self, record: TimelapseRecord) -> int:
        conn = self._conn()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO timelapses
                    (camera_id, name, state, mode, interval_seconds, output_fps,
                     frames_captured, created_ts, start_ts, end_ts, frames_dir,
                     video_path, thumb_path, size_bytes, width, height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.camera_id, record.name, record.state, record.mode,
                    record.interval_seconds, record.output_fps, record.frames_captured,
                    record.created_ts, record.start_ts, record.end_ts, record.frames_dir,
                    record.video_path, record.thumb_path, record.size_bytes,
                    record.width, record.height,
                ),
            )
            return int(cur.lastrowid or 0)

    def update_timelapse(self, tl_id: int, **fields: Any) -> None:
        """Patch arbitrary columns. Keys are code-controlled (never user input)."""
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE timelapses SET {cols} WHERE id=?", (*fields.values(), tl_id)
            )

    def get_timelapse(self, tl_id: int) -> TimelapseRecord | None:
        row = self._conn().execute("SELECT * FROM timelapses WHERE id=?", (tl_id,)).fetchone()
        return _timelapse_from_row(row) if row else None

    def list_timelapses(
        self, camera_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[TimelapseRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if camera_id:
            clauses.append("camera_id=?")
            params.append(camera_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._conn().execute(
            f"SELECT * FROM timelapses {where} ORDER BY created_ts DESC LIMIT ? OFFSET ?", params
        ).fetchall()
        return [_timelapse_from_row(r) for r in rows]

    def delete_timelapse(self, tl_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM timelapses WHERE id=?", (tl_id,))

    def interrupt_active_timelapses(self) -> int:
        """Mark non-terminal timelapses as interrupted at startup (their worker
        is gone). Frames stay on disk, so they can still be encoded. A smoothing
        pass cut short by the restart is reset to 'error' so it isn't stuck."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE timelapses SET state='interrupted' "
                "WHERE state IN ('capturing', 'encoding')"
            )
            conn.execute(
                "UPDATE timelapses SET smooth_state='error' WHERE smooth_state='processing'"
            )
            return cur.rowcount

    def total_timelapse_bytes(self) -> int:
        row = self._conn().execute(
            "SELECT COALESCE(SUM(size_bytes), 0) + COALESCE(SUM(smooth_size_bytes), 0) AS total "
            "FROM timelapses"
        ).fetchone()
        return int(row["total"])

    # -- datasets ----------------------------------------------------------
    def add_dataset(self, record: DatasetRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO datasets (name, task, created_ts, note) VALUES (?, ?, ?, ?)",
                (record.name, record.task, record.created_ts, record.note),
            )
            return int(cur.lastrowid or 0)

    def get_dataset(self, dataset_id: int) -> DatasetRecord | None:
        row = self._conn().execute(
            "SELECT * FROM datasets WHERE id=?", (dataset_id,)
        ).fetchone()
        return _dataset_from_row(row) if row else None

    def list_datasets(self) -> list[DatasetRecord]:
        rows = self._conn().execute("SELECT * FROM datasets ORDER BY created_ts DESC").fetchall()
        return [_dataset_from_row(r) for r in rows]

    def delete_dataset(self, dataset_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM dataset_samples WHERE dataset_id=?", (dataset_id,))
            conn.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))

    # -- dataset samples ---------------------------------------------------
    def add_sample(self, record: DatasetSampleRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO dataset_samples
                    (dataset_id, path, thumb, label, source, camera_id, host,
                     created_ts, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.dataset_id, record.path, record.thumb, record.label, record.source,
                    record.camera_id, record.host, record.created_ts, record.confidence,
                ),
            )
            return int(cur.lastrowid or 0)

    def get_sample(self, sample_id: int) -> DatasetSampleRecord | None:
        row = self._conn().execute(
            "SELECT * FROM dataset_samples WHERE id=?", (sample_id,)
        ).fetchone()
        return _sample_from_row(row) if row else None

    def list_samples(
        self, dataset_id: int, label: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[DatasetSampleRecord]:
        clauses = ["dataset_id=?"]
        params: list[Any] = [dataset_id]
        if label is not None:
            if label == "__unlabeled__":
                clauses.append("label IS NULL")
            else:
                clauses.append("label=?")
                params.append(label)
        params.extend([limit, offset])
        rows = self._conn().execute(
            f"SELECT * FROM dataset_samples WHERE {' AND '.join(clauses)} "
            "ORDER BY created_ts DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_sample_from_row(r) for r in rows]

    def set_sample_label(self, sample_id: int, label: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE dataset_samples SET label=?, confidence=NULL WHERE id=?", (label, sample_id)
            )

    def delete_sample(self, sample_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM dataset_samples WHERE id=?", (sample_id,))

    def total_sample_count(self) -> int:
        row = self._conn().execute("SELECT COUNT(*) AS n FROM dataset_samples").fetchone()
        return int(row["n"])

    def dataset_label_counts(self, dataset_id: int) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT label, COUNT(*) AS n FROM dataset_samples WHERE dataset_id=? GROUP BY label",
            (dataset_id,),
        ).fetchall()
        return {(r["label"] or "__unlabeled__"): int(r["n"]) for r in rows}

    # -- models ------------------------------------------------------------
    def add_model(self, record: ModelRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO models
                    (name, kind, path, classes_json, base_model, metrics_json, created_ts, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.name, record.kind, record.path, record.classes_json,
                    record.base_model, record.metrics_json, record.created_ts, record.active,
                ),
            )
            return int(cur.lastrowid or 0)

    def get_model(self, model_id: int) -> ModelRecord | None:
        row = self._conn().execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        return _model_from_row(row) if row else None

    def list_models(self) -> list[ModelRecord]:
        rows = self._conn().execute("SELECT * FROM models ORDER BY created_ts DESC").fetchall()
        return [_model_from_row(r) for r in rows]

    def active_model(self) -> ModelRecord | None:
        row = self._conn().execute("SELECT * FROM models WHERE active=1 LIMIT 1").fetchone()
        return _model_from_row(row) if row else None

    def set_active_model(self, model_id: int | None) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE models SET active=0")
            if model_id:
                conn.execute("UPDATE models SET active=1 WHERE id=?", (model_id,))

    def update_model(self, model_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(f"UPDATE models SET {cols} WHERE id=?", (*fields.values(), model_id))

    def delete_model(self, model_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM models WHERE id=?", (model_id,))

    # -- training runs -----------------------------------------------------
    def add_run(self, record: TrainingRunRecord) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO training_runs
                    (dataset_id, model_id, base_model, status, params_json, metrics_json, log,
                     epochs, epoch, created_ts, started_ts, ended_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.dataset_id, record.model_id, record.base_model, record.status,
                    record.params_json, record.metrics_json, record.log, record.epochs,
                    record.epoch, record.created_ts, record.started_ts, record.ended_ts,
                ),
            )
            return int(cur.lastrowid or 0)

    def get_run(self, run_id: int) -> TrainingRunRecord | None:
        row = self._conn().execute("SELECT * FROM training_runs WHERE id=?", (run_id,)).fetchone()
        return _run_from_row(row) if row else None

    def list_runs(self, limit: int = 50) -> list[TrainingRunRecord]:
        rows = self._conn().execute(
            "SELECT * FROM training_runs ORDER BY created_ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_run_from_row(r) for r in rows]

    def update_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(f"UPDATE training_runs SET {cols} WHERE id=?", (*fields.values(), run_id))

    def interrupt_active_runs(self) -> int:
        """Non-terminal runs whose worker is gone (restart/crash) → error."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE training_runs SET status='error' "
                "WHERE status IN ('queued', 'preparing', 'training')"
            )
            return cur.rowcount


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


def _timelapse_from_row(row: sqlite3.Row) -> TimelapseRecord:
    return TimelapseRecord(
        id=row["id"],
        camera_id=row["camera_id"],
        name=row["name"],
        state=row["state"],
        mode=row["mode"],
        interval_seconds=row["interval_seconds"],
        output_fps=row["output_fps"],
        frames_captured=row["frames_captured"],
        created_ts=row["created_ts"],
        start_ts=row["start_ts"],
        end_ts=row["end_ts"],
        frames_dir=row["frames_dir"],
        video_path=row["video_path"],
        thumb_path=row["thumb_path"],
        size_bytes=row["size_bytes"],
        width=row["width"],
        height=row["height"],
        smooth_state=row["smooth_state"],
        smooth_path=row["smooth_path"],
        smooth_size_bytes=row["smooth_size_bytes"],
        smooth_engine=row["smooth_engine"],
    )


def _dataset_from_row(row: sqlite3.Row) -> DatasetRecord:
    return DatasetRecord(
        id=row["id"],
        name=row["name"],
        task=row["task"],
        created_ts=row["created_ts"],
        note=row["note"],
    )


def _sample_from_row(row: sqlite3.Row) -> DatasetSampleRecord:
    return DatasetSampleRecord(
        id=row["id"],
        dataset_id=row["dataset_id"],
        path=row["path"],
        thumb=row["thumb"],
        label=row["label"],
        source=row["source"],
        camera_id=row["camera_id"],
        host=row["host"],
        created_ts=row["created_ts"],
        confidence=row["confidence"],
    )


def _model_from_row(row: sqlite3.Row) -> ModelRecord:
    return ModelRecord(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        path=row["path"],
        classes_json=row["classes_json"],
        base_model=row["base_model"],
        metrics_json=row["metrics_json"],
        created_ts=row["created_ts"],
        active=row["active"],
    )


def _run_from_row(row: sqlite3.Row) -> TrainingRunRecord:
    return TrainingRunRecord(
        id=row["id"],
        dataset_id=row["dataset_id"],
        model_id=row["model_id"],
        base_model=row["base_model"],
        status=row["status"],
        params_json=row["params_json"],
        metrics_json=row["metrics_json"],
        log=row["log"],
        epochs=row["epochs"],
        epoch=row["epoch"],
        created_ts=row["created_ts"],
        started_ts=row["started_ts"],
        ended_ts=row["ended_ts"],
    )


def now() -> float:
    return time.time()
