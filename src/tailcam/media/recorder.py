"""Per-camera recording sessions writing browser-playable H.264 mp4 files.

Frames are piped to ffmpeg (bundled or system) — see ``media.video_sink`` for
the fallback ladder when it's unavailable."""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from functools import partial
from pathlib import Path

import numpy as np

from tailcam import paths
from tailcam.camera.frame import FrameConsumer
from tailcam.camera.manager import CameraManager
from tailcam.logging_setup import get_logger
from tailcam.media.snapshot import _write_thumbnail
from tailcam.media.video_sink import VideoSink, open_video_sink
from tailcam.persistence.models import MediaRecord
from tailcam.persistence.store import Store

log = get_logger(__name__)


class _RecordingSession:
    def __init__(
        self,
        camera_id: str,
        buffer,
        fps: int,
        trigger: str,
        reacquire=None,
        media_camera_id: str | None = None,
        source_host: str = "",
    ) -> None:
        self.camera_id = camera_id  # session key (composite for remote captures)
        self.media_camera_id = media_camera_id or camera_id  # what the record says
        self.source_host = source_host
        self.buffer = buffer
        self._reacquire = reacquire  # follow the camera across a Restart
        self.fps = max(1, fps)
        self.trigger = trigger
        self.start_ts = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._writer: VideoSink | None = None
        self._writer_size: tuple[int, int] | None = None  # (w, h) locked at open
        self._resize_warned = False
        self._open_failed = False
        self.path: Path | None = None
        self._first_image = None
        self.frames_written = 0
        self.codec = ""
        self.error = ""  # why the session ended early (disk full, encoder died)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)

    def _run(self) -> None:
        # The encoder is told "one frame = 1/fps s", so the clip only plays at
        # real speed if we hand it exactly fps frames per wall-clock second:
        # drop extras when the camera is faster, repeat the last frame when it
        # is slower (a Pi camera negotiated down to 10 fps, a 5 fps remote
        # pull). Before this, slow sources produced clips that played 1.5-3x
        # fast.
        interval = 1.0 / self.fps
        next_due = time.monotonic()
        consumer = FrameConsumer(self.buffer, self._reacquire)
        last: np.ndarray | None = None
        try:
            while not self._stop.is_set():
                frame = consumer.next_frame(timeout=min(1.0, interval))
                if frame is not None:
                    last = frame.image
                elif consumer.ended:  # camera removed — end the recording
                    break
                if last is None:
                    continue
                now = time.monotonic()
                if now < next_due:
                    continue
                if self._writer is None and not self._open_failed:
                    self._open_writer(last)
                    if self._writer is None:
                        self.error = "could not open a video writer (disk full?)"
                        break
                # One write per elapsed slot (repeat the frame to fill gaps),
                # capped so a stall can't turn into a burst.
                slots = min(5, int((now - next_due) / interval) + 1)
                for _ in range(slots):
                    if self._writer is not None and self._writer.write(self._fit(last)):
                        self.frames_written += 1
                next_due += slots * interval
                if now - next_due > 2.0:  # fell far behind (suspend?) — resync
                    next_due = now
                if self._first_image is None:
                    self._first_image = last.copy()
                if self._writer is not None and getattr(self._writer, "failed", False):
                    self.error = "encoder stopped (disk full?)"
                    log.error("recording %s: %s", self.camera_id, self.error)
                    break
        finally:
            if self._writer is not None:
                if not self._writer.close():
                    log.error("recording %s: encoder reported failure", self.camera_id)
                    self.error = self.error or "encoder failed to finalize the clip"
                    self.frames_written = 0

    def _fit(self, image):
        """A VideoWriter is fixed to its first frame's size; a mid-recording
        resolution change (rotation, reconnect at new res) would otherwise
        silently drop every mismatched frame. Resize back to keep it continuous."""
        h, w = image.shape[:2]
        if self._writer_size is not None and (w, h) != self._writer_size:
            if not self._resize_warned:
                log.warning(
                    "recording %s: frame %sx%s != writer %s; resizing to keep the clip continuous",
                    self.camera_id, w, h, self._writer_size,
                )
                self._resize_warned = True
            import cv2

            return cv2.resize(image, self._writer_size)
        return image

    def _open_writer(self, image) -> None:
        h, w = image.shape[:2]
        stamp = datetime.fromtimestamp(self.start_ts).strftime("%Y%m%d-%H%M%S")
        raw_id = (
            f"{self.source_host.split('.')[0]}_{self.media_camera_id}"
            if self.source_host
            else self.media_camera_id
        )
        # Filename-safe on every OS (a remote id could carry ':' or '\\').
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_id).strip("._") or "camera"
        self.path = paths.media_dir() / f"{safe_id}_{stamp}.mp4"
        sink = open_video_sink(self.path, float(self.fps), (w, h))
        if sink is None:
            log.error("Failed to open a video writer for %s", self.path)
            self._open_failed = True
            return
        self._writer = sink
        self.codec = sink.codec
        self._writer_size = (w, h)


class RecordingService:
    def __init__(self, manager: CameraManager, store: Store) -> None:
        self._manager = manager
        self._store = store
        self._sessions: dict[str, _RecordingSession] = {}
        self._lock = threading.Lock()

    def is_recording(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._sessions

    def session_keys(self) -> list[str]:
        with self._lock:
            return list(self._sessions)

    def session_error(self, camera_id: str) -> str:
        """Why a still-registered session already stopped writing ("" if fine)."""
        with self._lock:
            session = self._sessions.get(camera_id)
        return session.error if session else ""

    def start(
        self,
        camera_id: str,
        fps: int = 15,
        trigger: str = "manual",
        buffer=None,
        reacquire=None,
        media_camera_id: str | None = None,
        source_host: str = "",
    ) -> bool:
        """Start recording ``camera_id`` (a local camera). A storage node
        recording a peer's camera passes the pulled ``buffer`` + ``reacquire``
        under a composite session key, plus the real camera id and owner
        (``source_host``) for the media record."""
        with self._lock:
            if camera_id in self._sessions:
                return False
            if buffer is None:
                buffer = self._manager.get_buffer(camera_id)
                reacquire = partial(self._manager.get_buffer, camera_id)
            if buffer is None:
                return False
            session = _RecordingSession(
                camera_id, buffer, fps, trigger,
                reacquire=reacquire,
                media_camera_id=media_camera_id,
                source_host=source_host,
            )
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
            camera_id=session.media_camera_id,
            media_type="recording",
            path=str(session.path),
            thumbnail=str(thumb) if thumb else None,
            created_ts=session.start_ts,
            trigger=session.trigger,
            size_bytes=session.path.stat().st_size,
            source_host=session.source_host,
        )
        record.id = self._store.add_media(record)
        return record

    def stop_all(self) -> None:
        """Finalize every session in parallel: each stop joins its thread and
        waits for its encoder, and a serial shutdown of several recordings
        blew past systemd's stop timeout (SIGKILL mid-finalize)."""
        keys = self.session_keys()
        threads = [
            threading.Thread(target=self.stop, args=(k,), name=f"rec-stop-{k}", daemon=True)
            for k in keys
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=150.0)
