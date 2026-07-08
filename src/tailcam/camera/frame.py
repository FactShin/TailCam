"""Frame container and the latest-only frame buffer.

``FrameBuffer`` is the decoupling seam between the capture thread (one producer)
and any number of consumers (MJPEG streams, recorder, motion worker). It only
ever retains the *latest* frame: a slow consumer drops intermediate frames and
can never back-pressure or stall the producer.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    """A single captured frame in BGR (OpenCV) order."""

    image: np.ndarray
    seq: int
    timestamp: float

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


class FrameBuffer:
    """Holds only the most recent frame; notifies waiters on each publish."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self._closed = False

    def publish(self, image: np.ndarray) -> Frame:
        with self._cond:
            seq = (self._frame.seq + 1) if self._frame else 0
            frame = Frame(image=image, seq=seq, timestamp=time.time())
            self._frame = frame
            self._cond.notify_all()
            return frame

    def latest(self) -> Frame | None:
        with self._cond:
            return self._frame

    def await_latest(self, last_seq: int, timeout: float = 1.0) -> Frame | None:
        """Block until a frame newer than ``last_seq`` arrives (or timeout/close).

        Returns the newest frame, or ``None`` on timeout or after close.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._closed:
                if self._frame is not None and self._frame.seq != last_seq:
                    return self._frame
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return None

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed


class FrameConsumer:
    """A camera-following read side of a FrameBuffer.

    A camera Restart/rescan replaces the worker's buffer with a fresh one and
    closes the old, so a consumer holding the old buffer would (a) busy-spin —
    ``await_latest`` returns None instantly once closed — and (b) silently
    never see the new camera. FrameConsumer re-acquires the live buffer from a
    callable (typically ``lambda: manager.get_buffer(camera_id)``) when its
    current one closes, so motion/recording/timelapse follow the camera across
    restarts. When re-acquire yields nothing (camera removed), it reports
    ``ended`` so the loop can exit instead of spinning.
    """

    def __init__(
        self, buffer: FrameBuffer, reacquire: Callable[[], FrameBuffer | None] | None = None
    ) -> None:
        self.buffer = buffer
        self._reacquire = reacquire
        self._last_seq = -1
        self.ended = False

    def next_frame(self, timeout: float = 1.0) -> Frame | None:
        """The next frame newer than the last one returned, or None on timeout.

        On timeout, if the buffer has closed, try to follow the camera to its
        new buffer; if that fails (removed), set ``ended``. Callers should
        ``break`` when ``ended`` and ``continue`` on a plain None.
        """
        frame = self.buffer.await_latest(self._last_seq, timeout)
        if frame is not None:
            self._last_seq = frame.seq
            return frame
        if self.buffer.closed:
            new = self._reacquire() if self._reacquire is not None else None
            if new is not None and not new.closed:
                self.buffer = new
                self._last_seq = -1  # fresh buffer restarts sequence at 0
            else:
                self.ended = True
        return None
