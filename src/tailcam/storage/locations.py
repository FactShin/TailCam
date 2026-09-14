"""Registered roots and transactional space reservations; no implicit mount recreation."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from tailcam.storage.catalog import ArtifactCatalog
from tailcam.storage.models import MAX_INTEGER, StorageError, StorageLocation

MARKER = ".tailcam-storage-location"


class LocationRegistry:
    def __init__(self, catalog: ArtifactCatalog) -> None:
        self.catalog = catalog

    def get(self, location_id: str | None = None) -> StorageLocation:
        sql = "SELECT data FROM storage_locations WHERE " + ("id=?" if location_id else "active=1")
        row = self.catalog.connection.execute(sql, (location_id,) if location_id else ()).fetchone()
        if row is None:
            raise StorageError(
                "location_missing", "No registered storage location is available.", 503
            )
        return StorageLocation.model_validate_json(row[0])

    def register(
        self,
        path: str,
        *,
        label: str = "",
        create: bool = False,
        quota_bytes: int = 0,
        reserve_bytes: int = 0,
        make_default: bool = True,
    ) -> StorageLocation:
        target = Path(path).expanduser()
        if not target.is_absolute() or any(
            type(v) is not int or not 0 <= v <= MAX_INTEGER for v in (quota_bytes, reserve_bytes)
        ):
            raise StorageError(
                "invalid_location", "Use an absolute path and nonnegative limits.", 422
            )
        # Creation is an explicit setup action only, never part of status/admission/startup checks.
        known = self.catalog.connection.execute(
            "SELECT data FROM storage_locations WHERE path=?", (str(target.resolve()),)
        ).fetchone()
        if known:
            self.verify(StorageLocation.model_validate_json(known[0]))
        if len(label) > 128:
            raise StorageError("invalid_location", "Location label exceeds 128 characters.", 422)
        if create and not known:
            target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir():
            raise StorageError("mount_missing", "The selected storage directory is missing.", 503)
        target = target.resolve(strict=True)
        existing = self.catalog.connection.execute(
            "SELECT data FROM storage_locations WHERE path=?", (str(target),)
        ).fetchone()
        if existing:
            location = StorageLocation.model_validate_json(existing[0])
            self.verify(location)
            if (location.quota_bytes, location.reserve_bytes) != (quota_bytes, reserve_bytes):
                location.quota_bytes, location.reserve_bytes = quota_bytes, reserve_bytes
            location.label = label or location.label
        else:
            marker_path = target / MARKER
            if marker_path.is_symlink():
                raise StorageError(
                    "mount_changed", "Storage identity is not a regular marker.", 503
                )
            if marker_path.exists():
                try:
                    if marker_path.stat().st_size > 1024:
                        raise ValueError
                    marker = json.loads(marker_path.read_text())
                    if (
                        not isinstance(marker, dict)
                        or marker.get("node_id") != self.catalog.node_id
                    ):
                        raise ValueError
                    location_id = str(marker["location_id"])
                    token = str(marker["token"])
                    if len(token) != 36 or len(location_id) != 36:
                        raise ValueError
                except (ValueError, KeyError, OSError):
                    raise StorageError(
                        "mount_changed", "Storage marker belongs to another root.", 503
                    ) from None
            else:
                location_id, token = str(uuid4()), str(uuid4())
                marker = {
                    "location_id": location_id,
                    "node_id": self.catalog.node_id,
                    "token": token,
                }
                with marker_path.open("x") as stream:
                    stream.write(json.dumps(marker))
                    stream.flush()
                    os.fsync(stream.fileno())
            info = target.stat()
            location = StorageLocation(
                location_id=location_id,
                node_id=self.catalog.node_id,
                label=label,
                path=str(target),
                marker=token,
                device=info.st_dev,
                inode=info.st_ino,
                created_at=time.time(),
                quota_bytes=quota_bytes,
                reserve_bytes=reserve_bytes,
            )
        location.is_default = make_default
        with self.catalog.transaction() as conn:
            if make_default:
                conn.execute("UPDATE storage_locations SET active=0")
            conn.execute(
                "INSERT INTO storage_locations VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "active=excluded.active,data=excluded.data",
                (
                    location.location_id,
                    location.path,
                    int(make_default),
                    location.model_dump_json(),
                ),
            )
        return self.describe(location.location_id)

    def verify(self, location: StorageLocation) -> Path:
        root = Path(location.path)
        try:
            info = root.stat()
            if root.is_symlink() or root.resolve(strict=True) != root:
                raise ValueError
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError
            if (info.st_dev, info.st_ino) != (location.device, location.inode):
                raise ValueError
            marker_path = root / MARKER
            if marker_path.is_symlink() or marker_path.stat().st_size > 1024:
                raise ValueError
            marker = json.loads(marker_path.read_text())
            if marker != {
                "location_id": location.location_id,
                "node_id": location.node_id,
                "token": location.marker,
            }:
                raise ValueError
        except FileNotFoundError:
            raise StorageError(
                "mount_missing", "The registered storage mount is missing.", 503
            ) from None
        except (OSError, ValueError, TypeError):
            raise StorageError(
                "mount_changed", "The registered storage identity changed.", 503
            ) from None
        return root

    @contextmanager
    def root_handle(self, location_id: str):
        """Anchor POSIX transfer operations to the verified directory, even during an unmount."""
        location = self.get(location_id)
        root = self.verify(location)
        fd = None
        if os.open in os.supports_dir_fd:
            fd = os.open(
                root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (location.device, location.inode):
                os.close(fd)
                raise StorageError("mount_changed", "Storage changed during admission.", 503)
        try:
            yield root, fd
        finally:
            if fd is not None:
                os.close(fd)

    def accounting(self, location_id: str, *, connection=None) -> tuple[int, int]:
        conn = connection or self.catalog.connection
        used = conn.execute(
            "SELECT COALESCE(SUM(size),0) FROM storage_copies WHERE location=?", (location_id,)
        ).fetchone()[0]
        reserved = conn.execute(
            "SELECT COALESCE(SUM(bytes),0) FROM storage_reservations WHERE location=?",
            (location_id,),
        ).fetchone()[0]
        return int(used), int(reserved)

    def reserve(
        self, token: str, location_id: str, size: int, category: str, *, connection=None
    ) -> None:
        if type(size) is not int or not 0 <= size <= MAX_INTEGER:
            raise StorageError("invalid_size", "Invalid storage reservation.", 422)
        if connection is None:
            with self.catalog.transaction() as conn:
                self.reserve(token, location_id, size, category, connection=conn)
            return
        location = self.get(location_id)
        root = self.verify(location)
        old = connection.execute(
            "SELECT location,bytes FROM storage_reservations WHERE id=?", (token,)
        ).fetchone()
        if old:
            if (old[0], old[1]) != (location_id, size):
                raise StorageError("reservation_conflict", "Reservation identity already exists.")
            return
        used, reserved = self.accounting(location_id, connection=connection)
        free = shutil.disk_usage(root).free
        if location.quota_bytes and used + reserved + size > location.quota_bytes:
            raise StorageError("quota_exceeded", "Storage quota cannot admit this artifact.", 507)
        if free - reserved - size < location.reserve_bytes:
            raise StorageError("reserve_exceeded", "Reserved free space would be consumed.", 507)
        connection.execute(
            "INSERT INTO storage_reservations VALUES(?,?,?,?,?,?)",
            (token, location_id, size, category, None, time.time()),
        )

    def release(self, token: str, *, connection=None) -> None:
        conn = connection or self.catalog.connection
        conn.execute("DELETE FROM storage_reservations WHERE id=?", (token,))
        if connection is None:
            conn.commit()

    def describe(self, location_id: str | None = None) -> StorageLocation:
        location = self.get(location_id)
        active = self.catalog.connection.execute(
            "SELECT active FROM storage_locations WHERE id=?", (location.location_id,)
        ).fetchone()[0]
        location.is_default = bool(active)
        location.used_bytes, location.reserved_bytes = self.accounting(location.location_id)
        try:
            root = self.verify(location)
            location.free_bytes = shutil.disk_usage(root).free
            available = max(
                0, location.free_bytes - location.reserve_bytes - location.reserved_bytes
            )
            if location.quota_bytes:
                available = min(
                    available,
                    max(0, location.quota_bytes - location.used_bytes - location.reserved_bytes),
                )
            location.allocatable_bytes = available
            location.state = "ready" if os.access(root, os.W_OK | os.X_OK) else "unwritable"
        except StorageError as exc:
            location.state = "missing" if exc.code == "mount_missing" else "changed"
        return location

    def list(self) -> list[StorageLocation]:
        rows = self.catalog.connection.execute(
            "SELECT id FROM storage_locations ORDER BY rowid"
        ).fetchall()
        return [self.describe(row[0]) for row in rows]

    def update(
        self,
        location_id: str,
        *,
        label: str | None = None,
        quota_bytes: int | None = None,
        reserve_bytes: int | None = None,
        make_default: bool | None = None,
    ) -> StorageLocation:
        with self.catalog.transaction() as conn:
            location = self.get(location_id)
            root = self.verify(location)
            if label is not None:
                if len(label) > 128:
                    raise StorageError("invalid_location", "Location label is too long.", 422)
                location.label = label
            for name, value in (("quota_bytes", quota_bytes), ("reserve_bytes", reserve_bytes)):
                if value is not None:
                    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
                        raise StorageError(
                            "invalid_location", "Storage limits must be nonnegative.", 422
                        )
                    setattr(location, name, value)
            used, reserved = self.accounting(location_id, connection=conn)
            if location.quota_bytes and used + reserved > location.quota_bytes:
                raise StorageError(
                    "quota_exceeded", "Quota is below committed and reserved bytes.", 507
                )
            if shutil.disk_usage(root).free - reserved < location.reserve_bytes:
                raise StorageError(
                    "reserve_exceeded", "Free-space reserve is not currently available.", 507
                )
            active = bool(
                conn.execute(
                    "SELECT active FROM storage_locations WHERE id=?", (location_id,)
                ).fetchone()[0]
            )
            if make_default is not None:
                active = make_default
                if active:
                    conn.execute("UPDATE storage_locations SET active=0")
            location.is_default = active
            conn.execute(
                "UPDATE storage_locations SET active=?,data=? WHERE id=?",
                (
                    int(active),
                    location.model_dump_json(),
                    location_id,
                ),
            )
        return self.describe(location_id)
