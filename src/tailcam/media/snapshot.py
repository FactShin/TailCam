"""Capture still images from a camera's latest frame."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import cv2

from tailcam import paths
from tailcam.camera.manager import CameraManager
from tailcam.persistence.models import MediaRecord
from tailcam.persistence.store import Store
from tailcam.streaming.encoder import encode_jpeg

_THUMB_WIDTH = 320


class SnapshotService:
    def __init__(self, manager: CameraManager, store: Store) -> None:
        self._manager = manager
        self._store = store

    def capture(self, camera_id: str, trigger: str = "manual") -> MediaRecord | None:
        buffer = self._manager.get_buffer(camera_id)
        if buffer is None:
            return None
        frame = buffer.await_latest(-1, timeout=3.0)
        if frame is None:
            return None

        ts = time.time()
        stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        safe_id = camera_id.replace("/", "_")
        filename = f"{safe_id}_{stamp}.jpg"
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
        thumb_path = paths.thumbnails_dir() / (Path(source_filename).stem + ".jpg")
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(encode_jpeg(thumb, quality=75))
        return thumb_path
    except Exception:
        return None
