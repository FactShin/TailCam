"""Capture still images from a camera's latest frame."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2

from tailcam import paths
from tailcam.camera.manager import CameraManager
from tailcam.media.storage import alias, enabled, local_path, safe_filename, store_image
from tailcam.persistence.models import MediaRecord
from tailcam.persistence.store import Store
from tailcam.streaming.encoder import encode_jpeg

_THUMB_WIDTH = 320


class SnapshotService:
    def __init__(
        self,
        manager: CameraManager,
        store: Store,
        role_check: Callable[[], None] | None = None,
        storage_service=None,
    ) -> None:
        self._manager = manager
        self._store = store
        self._role_check = role_check
        self._storage_service = storage_service

    def capture(self, camera_id: str, trigger: str = "manual") -> MediaRecord | None:
        use_storage = enabled(self._storage_service)
        if self._role_check is not None and not use_storage:
            self._role_check()
        buffer = self._manager.get_buffer(camera_id)
        if buffer is None:
            return None
        frame = buffer.await_latest(-1, timeout=3.0)
        if frame is None:
            return None

        if use_storage:
            service = self._storage_service
            artifact, thumb = store_image(
                service,
                "snapshot",
                frame.image,
                camera_id=camera_id,
                quality=92,
                thumb_width=320,
                metadata={"trigger": trigger},
            )
            record = MediaRecord(
                id=None,
                camera_id=camera_id,
                media_type="snapshot",
                path=local_path(service, artifact),
                thumbnail=local_path(service, thumb) or None,
                created_ts=time.time(),
                trigger=trigger,
                size_bytes=artifact.size_bytes,
            )
            record.id = self._store.add_media(record)
            alias(service, "media", record.id, "file", artifact)
            alias(service, "media", record.id, "thumbnail", thumb)
            return record

        ts = time.time()
        stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        safe_id = safe_filename(camera_id)
        filename = f"{safe_id}_{stamp}.jpg"
        paths.require_media_root()
        path = paths.media_dir() / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode_jpeg(frame.image, quality=92))

        thumb_path = _write_thumbnail(frame.image, filename)
        record = MediaRecord(
            id=None,
            camera_id=camera_id,
            media_type="snapshot",
            path=str(path),
            thumbnail=str(thumb_path) if thumb_path else None,
            created_ts=ts,
            trigger=trigger,
            size_bytes=path.stat().st_size,
        )
        record.id = self._store.add_media(record)
        return record


def _write_thumbnail(image, source_filename: str) -> Path | None:
    try:
        h, w = image.shape[:2]
        scale = _THUMB_WIDTH / max(1, w)
        thumb = cv2.resize(image, (_THUMB_WIDTH, max(1, int(h * scale))))
        paths.require_media_root()
        thumb_path = paths.thumbnails_dir() / (Path(source_filename).stem + ".jpg")
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(encode_jpeg(thumb, quality=75))
        return thumb_path
    except Exception:
        return None
