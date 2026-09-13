"""Expiring content holds and cross-process serialization of owner mutations."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, cast
from uuid import UUID, uuid5

from tailcam.storage.models import ArtifactPin, StorageError


class ArtifactPins:
    def __init__(self, catalog):
        self.catalog = catalog
        self._held = threading.local()
        catalog.connection.execute(
            "CREATE TABLE IF NOT EXISTS storage_artifact_pins ("
            "artifact_id TEXT NOT NULL,pin_id TEXT NOT NULL,coordinator TEXT NOT NULL,"
            "expires REAL NOT NULL,data TEXT NOT NULL,PRIMARY KEY(artifact_id,pin_id))"
        )
        catalog.connection.commit()

    @contextmanager
    def guard(self, artifact_id: str):
        identifier = str(UUID(artifact_id))
        held: set[str] = getattr(self._held, "identifiers", set())
        if identifier in held:
            yield
            return
        with self._file_guard(identifier):
            self._held.identifiers = held | {identifier}
            try:
                yield
            finally:
                self._held.identifiers = held

    @contextmanager
    def _file_guard(self, artifact_id: str):
        """No SQLite transaction is retained during a potentially remote transfer.

        OS locks release on process death, so interrupted migration never leaves a stale
        mutex. Keep lock files in the private state directory: unlinking a live lock file
        would let another process lock a different inode for the same artifact.
        """
        identifier = str(UUID(artifact_id))
        directory = self.catalog.store.db_path.parent / "artifact-locks"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / identifier).open("a+b") as stream:
            if os.name == "nt":
                import msvcrt

                windows = cast(Any, msvcrt)
                locking = windows.locking

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                try:
                    locking(stream.fileno(), windows.LK_NBLCK, 1)
                except OSError:
                    raise StorageError(
                        "artifact_busy", "Artifact is being changed; retry shortly.", 503
                    ) from None
                try:
                    yield
                finally:
                    stream.seek(0)
                    locking(stream.fileno(), windows.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise StorageError(
                        "artifact_busy", "Artifact is being changed; retry shortly.", 503
                    ) from None
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def require_unpinned(self, artifact_id: str) -> None:
        tables = {
            row[0]
            for row in self.catalog.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for pin in self.catalog.connection.execute(
            "SELECT pin_id,coordinator FROM storage_artifact_pins "
            "WHERE artifact_id=? AND expires>?",
            (artifact_id, time.time()),
        ).fetchall():
            terminal = False
            if pin[1] == self.catalog.node_id and {"workload_pins", "workload_jobs"}.issubset(
                tables
            ):
                job = self.catalog.connection.execute(
                    "SELECT j.id FROM workload_pins w JOIN workload_jobs j ON j.id=w.job "
                    "WHERE w.artifact=? AND json_extract(w.pin,'$.pin_id')=?",
                    (artifact_id, pin[0]),
                ).fetchone()
                if job:
                    lock_id = str(uuid5(UUID(self.catalog.node_id), "job-admission:" + job[0]))
                    try:
                        # A retry holds this mutex from reacquiring inputs through requeue.
                        # Both lock orders are nonblocking: deletion cannot race that window.
                        with self.guard(lock_id):
                            current = self.catalog.connection.execute(
                                "SELECT state FROM workload_jobs WHERE id=?", (job[0],)
                            ).fetchone()
                            terminal = bool(
                                current
                                and current[0]
                                in {"succeeded", "failed", "cancelled", "deadline_exceeded"}
                            )
                    except StorageError:
                        pass
            if not terminal:
                raise StorageError("artifact_in_use", "An active job holds this artifact.")

    def pin(self, artifact_id: str, value: ArtifactPin | dict) -> ArtifactPin:
        pin = ArtifactPin.model_validate(value)
        now = time.time()
        if not now < pin.expires_at <= now + 604800:
            raise StorageError(
                "invalid_pin_expiry", "Artifact hold must expire within seven days.", 422
            )
        with self.guard(artifact_id), self.catalog.transaction() as conn:
            artifact = self.catalog.get(artifact_id)
            if artifact is None or artifact.state not in {"committed", "replicated"}:
                raise StorageError("artifact_missing", "Committed artifact is unavailable.", 404)
            if artifact.owner_node_id != self.catalog.node_id:
                raise StorageError("remote_owner", "Create this hold through its content owner.")
            if (artifact.sha256, artifact.size_bytes) != (pin.sha256, pin.size_bytes):
                raise StorageError(
                    "input_changed", "Content does not match its immutable reference."
                )
            conn.execute("DELETE FROM storage_artifact_pins WHERE expires<=?", (now,))
            old = conn.execute(
                "SELECT data FROM storage_artifact_pins WHERE artifact_id=? AND pin_id=?",
                (artifact_id, pin.pin_id),
            ).fetchone()
            if old:
                if ArtifactPin.model_validate_json(old[0]) != pin:
                    raise StorageError(
                        "pin_conflict", "Hold identity belongs to a different request."
                    )
                return pin
            if conn.execute("SELECT count(*) FROM storage_artifact_pins").fetchone()[0] >= 100000:
                raise StorageError("pin_capacity", "Owner artifact hold capacity is full.", 503)
            conn.execute(
                "INSERT INTO storage_artifact_pins VALUES(?,?,?,?,?)",
                (
                    artifact_id,
                    pin.pin_id,
                    pin.coordinator_node_id,
                    pin.expires_at,
                    pin.model_dump_json(),
                ),
            )
        return pin

    def release(self, artifact_id: str, pin_id: str, coordinator_node_id: str) -> bool:
        pin_id, coordinator = str(UUID(pin_id)), str(UUID(coordinator_node_id))
        with self.guard(artifact_id), self.catalog.transaction() as conn:
            previous = conn.execute(
                "SELECT coordinator FROM storage_artifact_pins WHERE artifact_id=? AND pin_id=?",
                (artifact_id, pin_id),
            ).fetchone()
            if previous and previous[0] != coordinator:
                raise StorageError(
                    "pin_owner_mismatch",
                    "Only the creating coordinator can release this hold.",
                    403,
                )
            return bool(
                conn.execute(
                    "DELETE FROM storage_artifact_pins "
                    "WHERE artifact_id=? AND pin_id=? AND coordinator=?",
                    (artifact_id, pin_id, coordinator),
                ).rowcount
            )
