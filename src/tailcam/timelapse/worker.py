"""Per-timelapse capture thread: grabs one frame every ``interval`` seconds."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from tailcam.camera.frame import FrameBuffer
from tailcam.logging_setup import get_logger
from tailcam.streaming.encoder import encode_jpeg

log = get_logger(__name__)


class TimelapseCaptureWorker:
    """Saves numbered JPEG frames at a fixed cadence until stopped.

    Raw frames (not just an encoded video) are written so post-processing can
    later re-stitch them. ``on_complete`` fires only when capture ends on its
    own (duration/frame cap reached) — an external ``stop()`` does not call it,
    so the service can drive finalization itself.
    """

    def __init__(
        self,
        tl_id: int,
        camera_id: str,
        buffer: FrameBuffer,
        frames_dir: Path,
        interval_seconds: float,
        jpeg_quality: int,
        max_frames: int = 0,
        duration_seconds: float = 0.0,
        on_frame: Callable[[int], None] | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        self.tl_id = tl_id
        self.camera_id = camera_id
        self.buffer = buffer
        self.frames_dir = frames_dir
        self.interval = max(0.1, interval_seconds)
        self.jpeg_quality = jpeg_quality
        self.max_frames = max_frames
        self.duration = duration_seconds
        self._on_frame = on_frame
        self._on_complete = on_complete
        self.frames_captured = 0
        self.width = 0
        self.height = 0
        self._first = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"timelapse-{tl_id}", daemon=True)

    def start(self) -> None:
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=12.0)

    def _run(self) -> None:
        last_seq = -1
        next_due = time.monotonic()
        deadline = (time.monotonic() + self.duration) if self.duration else None
        natural = False
        while not self._stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                natural = True
                break
            frame = self.buffer.await_latest(last_seq, timeout=1.0)
            if frame is None:
                continue
            last_seq = frame.seq
            now = time.monotonic()
            if now < next_due:
                continue
            next_due = now + self.interval
            self._save(frame.image)
            if self.max_frames and self.frames_captured >= self.max_frames:
                natural = True
                break
        if natural and self._on_complete is not None:
            self._on_complete()

    def _save(self, image: np.ndarray) -> None:
        try:
            path = self.frames_dir / f"{self.frames_captured:06d}.jpg"
            path.write_bytes(encode_jpeg(image, self.jpeg_quality))
        except Exception as exc:  # pragma: no cover - disk full etc.
            log.warning("timelapse %s: failed to save frame: %s", self.tl_id, exc)
            return
        if self._first:
            self.height, self.width = image.shape[:2]
            self._first = False
        self.frames_captured += 1
        if self._on_frame is not None:
            self._on_frame(self.frames_captured)
