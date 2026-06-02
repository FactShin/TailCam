"""Frame container and the latest-only frame buffer.

``FrameBuffer`` is the decoupling seam between the capture thread (one producer)
and any number of consumers (MJPEG streams, recorder, motion worker). It only
ever retains the *latest* frame: a slow consumer drops intermediate frames and
can never back-pressure or stall the producer.
"""

from __future__ import annotations

import threading
import time
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
