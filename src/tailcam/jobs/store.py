"""Embedded job journal; transactions fence scheduling and all result transitions."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager

from tailcam.jobs.models import JobRecord, JobSpec

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workload_jobs (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, idem TEXT NOT NULL, digest TEXT NOT NULL,
 state TEXT NOT NULL, priority INTEGER NOT NULL, created REAL NOT NULL,
 spec TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(scope,idem)
);
CREATE INDEX IF NOT EXISTS workload_job_queue ON workload_jobs(state,priority,created);
CREATE TABLE IF NOT EXISTS workload_stages (
 job TEXT NOT NULL, stage TEXT NOT NULL, state TEXT NOT NULL,
 lease TEXT, permit TEXT, retry_at REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(job,stage)
);
CREATE TABLE IF NOT EXISTS workload_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workload_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workload_providers (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workload_pins (
 job TEXT NOT NULL,artifact TEXT NOT NULL,reference TEXT NOT NULL,pin TEXT NOT NULL,
 PRIMARY KEY(job,artifact)
);
"""


class JobStore:
    def __init__(self, store):
        self.store = store
        self.connection.executescript(_SCHEMA)

    @property
    def connection(self):
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

    def get(self, job_id: str) -> JobRecord | None:
        row = self.connection.execute(
            "SELECT data FROM workload_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return JobRecord.model_validate_json(row[0]) if row else None

    def spec(self, job_id: str) -> JobSpec | None:
        row = self.connection.execute(
            "SELECT spec FROM workload_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return JobSpec.model_validate_json(row[0]) if row else None

    def save(self, record: JobRecord, conn, event: str | None = None):
        record.revision += 1
        conn.execute(
            "UPDATE workload_jobs SET state=?,data=? WHERE id=?",
            (record.state, record.model_dump_json(), record.job_id),
        )
        if event:
            self.event(
                record.job_id, event, conn, {"state": record.state, "revision": record.revision}
            )

    def event(self, job_id: str, kind: str, conn, detail: dict | None = None):
        conn.execute(
            "INSERT INTO workload_events(job,data) VALUES(?,?)",
            (
                job_id,
                json.dumps(
                    {
                        "job_id": job_id,
                        "kind": kind,
                        "created_at": time.time(),
                        **(detail or {}),
                    },
                    allow_nan=False,
                ),
            ),
        )

    def list(self, *, state=None, task=None, limit=50, offset=0):
        limit, offset = min(100, max(1, limit)), max(0, offset)
        terms, args = [], []
        if state:
            terms.append("state=?")
            args.append(state)
        if task:
            terms.append("json_extract(data,'$.task')=?")
            args.append(task)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        rows = self.connection.execute(
            "SELECT data FROM workload_jobs" + where + " ORDER BY created DESC,id LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return [JobRecord.model_validate_json(row[0]) for row in rows]

    def events(self, job_id: str, *, after=0, limit=100):
        rows = self.connection.execute(
            "SELECT sequence,data FROM workload_events WHERE job=? AND sequence>? "
            "ORDER BY sequence LIMIT ?",
            (job_id, max(0, after), min(100, max(1, limit))),
        ).fetchall()
        return {
            "items": [{"event_id": str(row[0]), **json.loads(row[1])} for row in rows],
            "cursor": rows[-1][0] if rows else after,
        }

    def setting(self, key, default=None):
        row = self.connection.execute(
            "SELECT value FROM workload_settings WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value, conn=None):
        connection = conn if conn is not None else self.connection
        connection.execute(
            "INSERT OR REPLACE INTO workload_settings VALUES(?,?)",
            (key, json.dumps(value, allow_nan=False)),
        )
        if conn is None:
            connection.commit()
