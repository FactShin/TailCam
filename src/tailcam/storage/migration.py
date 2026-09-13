"""Reviewed, restartable copy/move of registered content; never whole-folder moves."""

from __future__ import annotations

import builtins
import hashlib
import json
import mimetypes
import os
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

from tailcam.storage.models import Artifact, DestinationRef, StorageError, StorageLocation

if TYPE_CHECKING:
    from tailcam.persistence.store import Store
    from tailcam.storage.service import StorageService

_MAX_ITEMS = 10000


class MigrationService:
    def __init__(
        self,
        storage: StorageService,
        store: Store,
        *,
        resolve_peer: Callable[[str], str | None] | None = None,
    ) -> None:
        self.storage, self.store, self.resolve_peer = storage, store, resolve_peer
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_job: str | None = None
        self.store._conn().execute(
            "CREATE TABLE IF NOT EXISTS storage_migrations "
            "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.store._conn().execute(
            "CREATE TABLE IF NOT EXISTS storage_migration_previews "
            "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.store._conn().commit()
        # Resumption is explicit; a restart must not silently restart deletion.
        for job in self._jobs():
            if job["state"] in {"running", "queued"}:
                job.update(
                    state="paused", phase="paused", detail="Interrupted; resume the reviewed plan"
                )
                self._save(job)

    def _save(self, value: dict, *, preview: bool = False) -> None:
        table = "storage_migration_previews" if preview else "storage_migrations"
        key = value["preview_id"] if preview else value["migration_id"]
        with self.store._conn() as conn:
            conn.execute(
                f"INSERT INTO {table} VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, json.dumps(value, allow_nan=False)),
            )

    def _load(self, key: str, *, preview: bool = False) -> dict:
        table = "storage_migration_previews" if preview else "storage_migrations"
        row = self.store._conn().execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        if row is None:
            raise StorageError("migration_missing", "Migration plan was not found.", 404)
        return json.loads(row[0])

    def _public(self, job: dict) -> dict:
        result = {
            k: v
            for k, v in job.items()
            if k not in {"manifest", "source_identity", "target_identity"}
        }
        result["can_cancel"] = job["state"] in {"queued", "running", "paused", "failed"}
        result["can_resume"] = (
            job["state"] in {"paused", "failed", "cancelled"}
            and self._active_job != job["migration_id"]
        )
        return result

    def get(self, migration_id: str) -> dict:
        return self._public(self._load(migration_id))

    def _jobs(self) -> builtins.list[dict]:
        rows = (
            self.store._conn()
            .execute("SELECT data FROM storage_migrations ORDER BY rowid")
            .fetchall()
        )
        return [json.loads(row[0]) for row in rows]

    def list(self) -> builtins.list[dict]:
        return [self._public(job) for job in reversed(self._jobs()[-100:])]

    def _fingerprint(self, path: Path, location: StorageLocation) -> tuple[os.stat_result, str]:
        """Read only a stable regular file beneath the registered source root."""
        root = self.storage.locations.verify(location)
        relative = path.relative_to(root)
        if not relative.parts or ".." in relative.parts or path.resolve(strict=True) != path:
            raise ValueError("source path changed")
        with self.storage.locations.root_handle(location.location_id) as (_, root_fd):
            parents: builtins.list[int] = []
            directory_fd = root_fd
            try:
                if directory_fd is not None:
                    for component in relative.parts[:-1]:
                        directory_fd = os.open(
                            component,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                        parents.append(directory_fd)
                filename = relative.name if directory_fd is not None else path
                kwargs = {"dir_fd": directory_fd} if directory_fd is not None else {}
                descriptor = os.open(
                    filename,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                    **kwargs,
                )
                with os.fdopen(descriptor, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError("source is not regular")
                    digest, size = hashlib.sha256(), 0
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > before.st_size:
                            raise ValueError("source changed while reading")
                        digest.update(chunk)
                    after = os.fstat(stream.fileno())
                    named = os.stat(filename, follow_symlinks=False, **kwargs)

                    def key(value: os.stat_result) -> tuple[int, ...]:
                        return (
                            value.st_dev,
                            value.st_ino,
                            value.st_size,
                            value.st_mtime_ns,
                            value.st_ctime_ns,
                        )

                    if (
                        key(before) != key(after)
                        or key(before) != key(named)
                        or size != before.st_size
                    ):
                        raise ValueError("source changed while reading")
                if path.resolve(strict=True) != path:
                    raise ValueError("source path changed")
                self.storage.locations.verify(location)
                return before, digest.hexdigest()
            finally:
                for descriptor in reversed(parents):
                    os.close(descriptor)

    def _alias_active(self, namespace: str, legacy_id: str) -> bool:
        conn = self.store._conn()
        if namespace == "timelapse":
            row = conn.execute(
                "SELECT state,smooth_state FROM timelapses WHERE id=?", (legacy_id,)
            ).fetchone()
            return bool(row and (row[0] in {"capturing", "encoding"} or row[1] == "processing"))
        if namespace == "motion":
            return (
                conn.execute(
                    "SELECT 1 FROM motion_events WHERE id=? AND end_ts IS NULL", (legacy_id,)
                ).fetchone()
                is not None
            )
        if namespace == "media":
            return (
                conn.execute(
                    "SELECT 1 FROM motion_events WHERE recording_id=? AND end_ts IS NULL",
                    (legacy_id,),
                ).fetchone()
                is not None
            )
        if namespace == "model":
            return (
                conn.execute(
                    "SELECT 1 FROM models WHERE id=? AND active=1", (legacy_id,)
                ).fetchone()
                is not None
            )
        if namespace == "sample":
            return (
                conn.execute(
                    "SELECT 1 FROM dataset_samples s "
                    "JOIN training_runs r ON r.dataset_id=s.dataset_id "
                    "WHERE s.id=? AND r.status IN ('queued','preparing','training')",
                    (legacy_id,),
                ).fetchone()
                is not None
            )
        return False

    def _artifact_active(self, artifact_id: str) -> bool:
        rows = (
            self.store._conn()
            .execute(
                "SELECT namespace,legacy_id FROM storage_aliases WHERE artifact_id=?",
                (artifact_id,),
            )
            .fetchall()
        )
        return any(self._alias_active(row[0], row[1]) for row in rows)

    def _legacy_files(self):
        conn = self.store._conn()
        for row in conn.execute("SELECT * FROM media ORDER BY id"):
            active = self._alias_active("media", str(row["id"]))
            yield (
                "media",
                str(row["id"]),
                "file",
                row["media_type"],
                row["path"],
                row["camera_id"],
                row["created_ts"],
                active,
            )
            if row["thumbnail"]:
                yield (
                    "media",
                    str(row["id"]),
                    "thumbnail",
                    "thumbnail",
                    row["thumbnail"],
                    row["camera_id"],
                    row["created_ts"],
                    active,
                )
        for row in conn.execute("SELECT * FROM timelapses ORDER BY id"):
            active = (
                row["state"] in {"capturing", "encoding"} or row["smooth_state"] == "processing"
            )
            for field, kind, variant in (
                ("video_path", "timelapse_video", "video"),
                ("smooth_path", "timelapse_smooth", "smooth"),
                ("thumb_path", "thumbnail", "thumbnail"),
            ):
                if row[field]:
                    yield (
                        "timelapse",
                        str(row["id"]),
                        variant,
                        kind,
                        row[field],
                        row["camera_id"],
                        row["created_ts"],
                        active,
                    )
            if row["frames_dir"]:
                directory = Path(row["frames_dir"])
                if directory.is_dir() and not directory.is_symlink():
                    for path in directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9].jpg"):
                        yield (
                            "timelapse",
                            str(row["id"]),
                            "frame/" + path.stem,
                            "timelapse_frame",
                            str(path),
                            row["camera_id"],
                            row["created_ts"],
                            active,
                        )
        for row in conn.execute("SELECT * FROM dataset_samples ORDER BY id"):
            for field, kind, variant in (
                ("path", "training_sample", "file"),
                ("thumb", "thumbnail", "thumbnail"),
            ):
                if row[field]:
                    yield (
                        "sample",
                        str(row["id"]),
                        variant,
                        kind,
                        row[field],
                        row["camera_id"],
                        row["created_ts"],
                        self._alias_active("sample", str(row["id"])),
                    )
        for row in conn.execute(
            "SELECT * FROM motion_events WHERE thumb_path IS NOT NULL ORDER BY id"
        ):
            yield (
                "motion",
                str(row["id"]),
                "thumbnail",
                "analysis_evidence",
                row["thumb_path"],
                row["camera_id"],
                row["start_ts"],
                row["end_ts"] is None,
            )
        for row in conn.execute(
            "SELECT * FROM models WHERE kind='trained' AND path!='' ORDER BY id"
        ):
            path = Path(row["path"])
            if path.is_file():
                yield (
                    "model",
                    str(row["id"]),
                    "file",
                    "model_output",
                    str(path),
                    "",
                    row["created_ts"],
                    bool(row["active"]),
                )

    def _index_legacy(self, location: StorageLocation) -> builtins.list[dict]:
        root = self.storage.locations.verify(location)
        excluded = []
        count = 0
        for (
            namespace,
            legacy_id,
            variant,
            kind,
            raw,
            camera,
            created,
            active,
        ) in self._legacy_files():
            path = Path(raw)
            try:
                path.relative_to(root)
            except ValueError:
                continue
            count += 1
            if count > _MAX_ITEMS:
                raise StorageError(
                    "migration_too_large",
                    "Select a smaller source; preview supports 10,000 files.",
                    422,
                )
            previous = self.storage.catalog.resolve_alias(namespace, legacy_id, variant)
            reason = "active output" if active else ""
            try:
                info = path.stat()
                if (
                    path.is_symlink()
                    or not stat.S_ISREG(info.st_mode)
                    or path.resolve(strict=True) != path
                ):
                    reason = "symbolic link or nonregular file"
                if info.st_size > self.storage.get_policy().artifact_max_bytes:
                    reason = "exceeds artifact size limit"
            except OSError:
                reason = "missing or unreadable file"
            if reason:
                excluded.append(
                    {
                        "artifact_id": previous.artifact_id if previous else "",
                        "name": path.name,
                        "kind": kind,
                        "size_bytes": 0,
                        "action": "skip",
                        "reason": reason,
                    }
                )
                continue
            if previous is not None:
                continue
            try:
                info, digest = self._fingerprint(path, location)
            except (OSError, ValueError, StorageError):
                excluded.append(
                    {
                        "artifact_id": "",
                        "name": path.name,
                        "kind": kind,
                        "size_bytes": 0,
                        "action": "skip",
                        "reason": "file changed or unreadable",
                    }
                )
                continue
            identity = str(
                uuid5(
                    NAMESPACE_URL,
                    f"tailcam:{self.storage.node_id}:{namespace}:{legacy_id}:{variant}",
                )
            )
            artifact = Artifact(
                artifact_id=identity,
                owner_node_id=self.storage.node_id,
                origin_node_id=self.storage.node_id,
                camera_id=camera,
                kind=kind,
                mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                size_bytes=info.st_size,
                sha256=digest,
                created_at=created,
                updated_at=time.time(),
                location_id=location.location_id,
                requested_destination=DestinationRef(
                    node_id=self.storage.node_id, location_id=location.location_id
                ),
                policy_revision=self.storage.get_policy().revision,
                metadata={
                    "name": path.name,
                    "legacy_namespace": namespace,
                    "legacy_id": legacy_id,
                    "legacy_variant": variant,
                },
            )
            self.storage.catalog.save(artifact, str(path))
            self.storage.catalog.alias(namespace, legacy_id, variant, identity)
        return excluded

    def _destination(self, destination: DestinationRef) -> StorageLocation:
        if destination.node_id == self.storage.node_id:
            location = self.storage.locations.describe(destination.location_id)
        else:
            base = self.resolve_peer(destination.node_id) if self.resolve_peer else None
            if base is None:
                raise StorageError("owner_unavailable", "Destination is unavailable.", 503)
            try:
                with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                    response = client.get(base + "/api/v1/storage/locations")
                    response.raise_for_status()
                    if len(response.content) > 1024 * 1024:
                        raise ValueError
                    items = response.json()["items"]
                if not isinstance(items, list) or len(items) > 100:
                    raise ValueError
                selected = (
                    next(x for x in items if x["location_id"] == destination.location_id)
                    if destination.location_id
                    else next(x for x in items if x.get("is_default"))
                )
                location = StorageLocation.model_validate(selected)
                if location.node_id != destination.node_id:
                    raise ValueError
            except (httpx.HTTPError, ValueError, KeyError, StopIteration, TypeError):
                raise StorageError(
                    "owner_unavailable", "Destination did not provide a valid location.", 503
                ) from None
        if location.state != "ready":
            raise StorageError("mount_changed", "Destination mount is not ready.", 503)
        return location

    def _check_destination(self, job: dict) -> None:
        target = self._destination(DestinationRef.model_validate(job["destination"]))
        previous = StorageLocation.model_validate(job["target_identity"])
        fields = ("location_id", "node_id", "path", "marker", "device", "inode")
        if any(getattr(target, field) != getattr(previous, field) for field in fields):
            raise StorageError("mount_changed", "The reviewed destination identity changed.", 503)

    def preview(
        self,
        source_location_id: str,
        destination: DestinationRef,
        *,
        kinds: builtins.list[str] | None = None,
        remove_source: bool = False,
    ) -> dict:
        source = self.storage.locations.get(source_location_id)
        target = self._destination(destination)
        destination = DestinationRef(node_id=target.node_id, location_id=target.location_id)
        if (destination.node_id, destination.location_id) == (source.node_id, source.location_id):
            raise StorageError("same_location", "Source and destination must differ.", 422)
        excluded = self._index_legacy(source)
        rows = (
            self.store._conn()
            .execute(
                "SELECT data,local_path FROM storage_artifacts WHERE owner=? AND location=? "
                "AND state IN ('committed','replicated') ORDER BY id LIMIT ?",
                (self.storage.node_id, source_location_id, _MAX_ITEMS + 1),
            )
            .fetchall()
        )
        if len(rows) > _MAX_ITEMS:
            raise StorageError(
                "migration_too_large",
                "Select a smaller source; preview supports 10,000 files.",
                422,
            )
        manifest: builtins.list[dict] = []
        items: builtins.list[dict] = []
        for row in rows:
            artifact = Artifact.model_validate_json(row[0])
            if kinds and artifact.kind not in kinds:
                continue
            path = Path(row[1] or "")
            if self._artifact_active(artifact.artifact_id):
                if not any(x["artifact_id"] == artifact.artifact_id for x in excluded):
                    excluded.append(
                        {
                            "artifact_id": artifact.artifact_id,
                            "name": path.name,
                            "kind": artifact.kind,
                            "size_bytes": artifact.size_bytes,
                            "action": "skip",
                            "reason": "active output",
                        }
                    )
                continue
            try:
                resolved = self.storage.resolve(artifact.artifact_id)
                info, digest = self._fingerprint(resolved, source)
                if (
                    resolved != path
                    or info.st_size != artifact.size_bytes
                    or digest != artifact.sha256
                ):
                    raise ValueError
            except (OSError, ValueError, StorageError):
                excluded.append(
                    {
                        "artifact_id": artifact.artifact_id,
                        "name": path.name,
                        "kind": artifact.kind,
                        "size_bytes": artifact.size_bytes,
                        "action": "skip",
                        "reason": "file changed or unavailable",
                    }
                )
                continue
            manifest.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes,
                    "path": str(path),
                    "mtime_ns": info.st_mtime_ns,
                    "inode": info.st_ino,
                    "device": info.st_dev,
                    "done": False,
                }
            )
            items.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "name": path.name,
                    "kind": artifact.kind,
                    "size_bytes": artifact.size_bytes,
                    "action": "move" if remove_source else "copy",
                }
            )
        total = sum(item["size_bytes"] for item in items)
        blockers = []
        if not items:
            blockers.append("No eligible committed content in this location")
        if target.allocatable_bytes is not None and total > target.allocatable_bytes:
            blockers.append("Destination cannot reserve enough space for this plan")
        if target.allocatable_bytes is None:
            blockers.append("Destination capacity is not currently available")
        result = {
            "preview_id": str(uuid4()),
            "expires_at": time.time() + 600,
            "source_location_id": source_location_id,
            "source_identity": source.model_dump(mode="json"),
            "target_identity": target.model_dump(mode="json"),
            "destination": destination.model_dump(mode="json"),
            "remove_source": remove_source,
            "item_count": len(items),
            "total_bytes": total,
            "can_start": not blockers,
            "blockers": blockers,
            "items": items + excluded,
            "manifest": manifest,
        }
        self._save(result, preview=True)
        return {
            k: v
            for k, v in result.items()
            if k not in {"manifest", "source_identity", "target_identity"}
        }

    def _verify_item(self, item: dict, source: StorageLocation) -> None:
        path = Path(item["path"])
        try:
            artifact = self.storage.catalog.get(item["artifact_id"])
            if (
                artifact is None
                or artifact.owner_node_id != self.storage.node_id
                or artifact.location_id != source.location_id
                or artifact.state not in {"committed", "replicated"}
            ):
                raise ValueError
            if self._artifact_active(item["artifact_id"]):
                raise ValueError
            info, digest = self._fingerprint(path, source)
            if (
                (info.st_size, info.st_mtime_ns, info.st_ino)
                != (
                    item["size_bytes"],
                    item["mtime_ns"],
                    item["inode"],
                )
                or info.st_dev != item.get("device", info.st_dev)
                or digest != item["sha256"]
            ):
                raise ValueError
        except (OSError, ValueError):
            raise StorageError(
                "preview_stale", "A reviewed source file changed; create a new preview."
            ) from None

    def _destination_committed(self, item: dict, job: dict) -> bool:
        artifact = self.storage.catalog.get(item["artifact_id"])
        destination = job["destination"]
        return bool(
            artifact
            and artifact.state in {"committed", "replicated"}
            and artifact.owner_node_id == destination["node_id"]
            and artifact.location_id == destination["location_id"]
            and artifact.sha256 == item["sha256"]
            and artifact.size_bytes == item["size_bytes"]
        )

    def start(self, preview_id: str) -> dict:
        with self._lock:
            # Double submission returns the same persistent operation.
            existing = (
                self.store._conn()
                .execute("SELECT data FROM storage_migrations WHERE id=?", (preview_id,))
                .fetchone()
            )
            if existing:
                return self._public(json.loads(existing[0]))
            preview = self._load(preview_id, preview=True)
            if not preview["can_start"] or preview["expires_at"] < time.time():
                raise StorageError("preview_stale", "Migration preview expired or cannot start.")
            self.storage.locations.verify(
                StorageLocation.model_validate(preview["source_identity"])
            )
            self._check_destination(preview)
            for item in preview["manifest"]:
                self._verify_item(item, StorageLocation.model_validate(preview["source_identity"]))
            job = {
                "migration_id": preview_id,
                "state": "queued",
                "phase": "queued",
                "completed_items": 0,
                "total_items": preview["item_count"],
                "bytes_done": 0,
                "total_bytes": preview["total_bytes"],
                "detail": "Reviewed copy queued",
                "remove_source": preview["remove_source"],
                "destination": preview["destination"],
                "source_identity": preview["source_identity"],
                "target_identity": preview["target_identity"],
                "manifest": preview["manifest"],
            }
            self._save(job)
        self._ensure_worker()
        return self.get(preview_id)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="storage-migration", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            while True:
                # Picking work and retiring the worker use the same lock as enqueue.
                # A start racing this exit therefore always creates a live worker.
                with self._lock:
                    if self._stop.is_set():
                        self._thread = None
                        return
                    pending = next((j for j in self._jobs() if j["state"] == "queued"), None)
                    if pending is None:
                        self._thread = None
                        return
                    key = pending["migration_id"]
                    self._active_job = key
                try:
                    self._process_job(key)
                except Exception:
                    # Includes unexpected implementation/transport errors. Never store
                    # exception text: it can contain peer addresses and private paths.
                    with self._lock:
                        job = self._load(key)
                        if job["state"] != "cancelled":
                            job.update(
                                state="failed",
                                phase="failed",
                                detail="Migration stopped; inspect storage availability "
                                "and resume or create a new preview",
                            )
                        self._save(job)
                finally:
                    with self._lock:
                        self._active_job = None
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    def _stopped(self, job: dict) -> bool:
        if job["state"] == "cancelled":
            return True
        if self._stop.is_set():
            job.update(
                state="paused",
                phase="paused",
                detail="Stopped between files; verified copies are preserved",
            )
            self._save(job)
            return True
        return False

    def _process_job(self, key: str) -> None:
        with self._lock:
            job = self._load(key)
            if self._stopped(job):
                return
            job.update(
                state="running", phase="copying", detail="Copying and verifying reviewed content"
            )
            self._save(job)
        for planned in job["manifest"]:
            with self._lock:
                latest = self._load(key)
                if self._stopped(latest):
                    return
                item = next(
                    x for x in latest["manifest"] if x["artifact_id"] == planned["artifact_id"]
                )
                if item["done"]:
                    continue
            self._check_destination(latest)
            if self._artifact_active(item["artifact_id"]):
                raise StorageError(
                    "active_output",
                    "A reviewed artifact is in use; wait for its producer to finish.",
                )
            if not self._destination_committed(item, latest):
                self._verify_item(item, StorageLocation.model_validate(latest["source_identity"]))
            with self._lock:
                if self._stopped(self._load(key)):
                    return
            artifact = self.storage.transfer_artifact(
                item["artifact_id"],
                DestinationRef.model_validate(latest["destination"]),
                remove_source=latest["remove_source"],
                source_location_id=latest["source_identity"]["location_id"],
            )
            if (
                artifact.sha256 != item["sha256"]
                or artifact.size_bytes != item["size_bytes"]
                or artifact.owner_node_id != latest["destination"]["node_id"]
                or artifact.location_id != latest["destination"]["location_id"]
                or artifact.state not in {"committed", "replicated"}
            ):
                raise StorageError(
                    "verification_failed", "Destination did not confirm the reviewed bytes."
                )
            with self._lock:
                # Cancellation can arrive during transfer. Merge progress into the
                # latest durable job instead of overwriting the cancellation state.
                latest = self._load(key)
                for recorded in latest["manifest"]:
                    if recorded["artifact_id"] == item["artifact_id"]:
                        recorded["done"] = True
                latest["completed_items"] = sum(bool(x["done"]) for x in latest["manifest"])
                latest["bytes_done"] = sum(x["size_bytes"] for x in latest["manifest"] if x["done"])
                self._save(latest)
                if self._stopped(latest):
                    return
        with self._lock:
            latest = self._load(key)
            if not self._stopped(latest):
                latest.update(
                    state="completed",
                    phase="completed",
                    detail="Reviewed content verified at destination",
                )
                self._save(latest)

    def cancel(self, migration_id: str) -> dict:
        with self._lock:
            job = self._load(migration_id)
            if job["state"] != "completed":
                job.update(
                    state="cancelled",
                    phase="cancelled",
                    detail="Cancellation requested; an in-flight file may finish verification",
                )
                self._save(job)
            return self._public(job)

    def resume(self, migration_id: str) -> dict:
        with self._lock:
            job = self._load(migration_id)
            if self._active_job == migration_id or job["state"] not in {
                "paused",
                "failed",
                "cancelled",
            }:
                raise StorageError(
                    "migration_active", "Wait for active work to stop before resuming."
                )
            self._check_destination(job)
            for item in job["manifest"]:
                if not item["done"]:
                    if self._artifact_active(item["artifact_id"]):
                        raise StorageError(
                            "active_output",
                            "A reviewed artifact is in use; wait for its producer to finish.",
                        )
                    # A prior attempt can have committed the destination and removed
                    # the source before saving progress. Replay finishes any cleanup.
                    if not self._destination_committed(item, job):
                        self._verify_item(
                            item, StorageLocation.model_validate(job["source_identity"])
                        )
            job.update(state="queued", phase="queued", detail="Reviewed migration queued to resume")
            self._save(job)
        self._ensure_worker()
        return self.get(migration_id)

    def shutdown(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=5)
