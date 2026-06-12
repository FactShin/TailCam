"""Per-camera recording sessions writing to disk via cv2.VideoWriter."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

from anycam import paths
from anycam.camera.manager import CameraManager
from anycam.logging_setup import get_logger
from anycam.media.snapshot import _write_thumbnail
from anycam.persistence.models import MediaRecord
from anycam.persistence.store import Store

log = get_logger(__name__)


class _RecordingSession:
    def __init__(self, camera_id: str, buffer, fps: int, trigger: str) -> None:
        self.camera_id = camera_id
        self.buffer = buffer
        self.fps = max(1, fps)
        self.trigger = trigger
        self.start_ts = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._writer: cv2.VideoWriter | None = None
        self.path: Path | None = None
        self._first_image = None
        self.frames_written = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)

    def _run(self) -> None:
        last_seq = -1
        interval = 1.0 / self.fps
        next_due = time.monotonic()
        while not self._stop.is_set():
            frame = self.buffer.await_latest(last_seq, timeout=1.0)
            if frame is None:
                continue
            last_seq = frame.seq
            now = time.monotonic()
            if now < next_due:
                continue
            next_due = now + interval
            if self._writer is None:
                self._open_writer(frame.image)
            if self._writer is not None:
                self._writer.write(frame.image)
                self.frames_written += 1
                if self._first_image is None:
                    self._first_image = frame.image.copy()
        if self._writer is not None:
            self._writer.release()

    def _open_writer(self, image) -> None:
        h, w = image.shape[:2]
        stamp = datetime.fromtimestamp(self.start_ts).strftime("%Y%m%d-%H%M%S")
        safe_id = self.camera_id.replace("/", "_")
        self.path = paths.media_dir() / f"{safe_id}_{stamp}.mp4"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
        self._writer = cv2.VideoWriter(str(self.path), fourcc, float(self.fps), (w, h))
        if not self._writer.isOpened():
            log.error("Failed to open VideoWriter for %s", self.path)
            self._writer = None


class RecordingService:
    def __init__(self, manager: CameraManager, store: Store) -> None:
        self._manager = manager
        self._store = store
        self._sessions: dict[str, _RecordingSession] = {}
        self._lock = threading.Lock()

    def is_recording(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._sessions

    def start(self, camera_id: str, fps: int = 15, trigger: str = "manual") -> bool:
        with self._lock:
            if camera_id in self._sessions:
                return False
            buffer = self._manager.get_buffer(camera_id)
            if buffer is None:
                return False
            session = _RecordingSession(camera_id, buffer, fps, trigger)
            session.start()
            self._sessions[camera_id] = session
            return True

    def stop(self, camera_id: str) -> MediaRecord | None:
        with self._lock:
            session = self._sessions.pop(camera_id, None)
        if session is None:
            return None
        session.stop()
        if session.path is None or not session.path.exists() or session.frames_written == 0:
            return None
        thumb = (
            _write_thumbnail(session._first_image, session.path.name)
            if session._first_image is not None
            else None
        )
        record = MediaRecord(
            id=None,
            camera_id=camera_id,
            media_type="recording",
            path=str(session.path),
            thumbnail=str(thumb) if thumb else None,
            created_ts=session.start_ts,
            trigger=session.trigger,
            size_bytes=session.path.stat().st_size,
        )
        record.id = self._store.add_media(record)
        return record

    def stop_all(self) -> None:
        for camera_id in list(self._sessions):
            self.stop(camera_id)
