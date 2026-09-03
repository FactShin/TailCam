"""MJPEG (multipart/x-mixed-replace) streaming backend.

The frame wait and JPEG encode both run off the event loop (in a worker thread)
so a busy camera or expensive encode never blocks FastAPI's async loop. The
latest-only ``FrameBuffer`` means slow clients simply drop frames.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator

import anyio

from tailcam.camera.frame import FrameBuffer
from tailcam.camera.transforms import StreamTransform
from tailcam.streaming.backend import StreamBackend
from tailcam.streaming.encoder import encode_jpeg

BOUNDARY = "frame"


class _EncodeCache:
    """One JPEG encode per (frame, transform, quality), shared by every viewer.

    The dashboard grid, the video wall, HomeKit's ffmpeg, and a phone can all
    watch the same camera at the same settings; without this each connection
    re-encoded every frame — on a Raspberry Pi that alone ate the CPU budget.
    Only the newest frame's encodes are kept, so memory stays a few hundred KB.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # id(buffer) -> (seq, {(transform, quality): jpeg})
        self._by_buffer: dict[int, tuple[int, dict[tuple[StreamTransform, int], bytes]]] = {}

    def get(self, buffer: FrameBuffer, seq: int, key: tuple[StreamTransform, int]) -> bytes | None:
        with self._lock:
            entry = self._by_buffer.get(id(buffer))
            if entry is None or entry[0] != seq:
                return None
            return entry[1].get(key)

    def put(
        self, buffer: FrameBuffer, seq: int, key: tuple[StreamTransform, int], jpeg: bytes
    ) -> None:
        with self._lock:
            entry = self._by_buffer.get(id(buffer))
            if entry is None or entry[0] != seq:
                entry = (seq, {})
                self._by_buffer[id(buffer)] = entry
            entry[1][key] = jpeg
            if len(self._by_buffer) > 64:  # closed buffers linger; keep it bounded
                for bid in list(self._by_buffer)[:-32]:
                    self._by_buffer.pop(bid, None)


class MJPEGBackend(StreamBackend):
    media_type = f"multipart/x-mixed-replace; boundary={BOUNDARY}"

    def __init__(self) -> None:
        self._cache = _EncodeCache()

    def _encode(
        self, buffer: FrameBuffer, seq: int, image, transform: StreamTransform, quality: int
    ) -> bytes:
        key = (transform, quality)
        cached = self._cache.get(buffer, seq, key)
        if cached is not None:
            return cached
        if transform != StreamTransform():
            image = transform.apply(image)
        jpeg = encode_jpeg(image, quality)
        self._cache.put(buffer, seq, key, jpeg)
        return jpeg

    async def stream(
        self,
        buffer: FrameBuffer,
        transform: StreamTransform,
        target_fps: int,
        quality: int,
    ) -> AsyncIterator[bytes]:
        last_seq = -1
        min_interval = 1.0 / max(1, target_fps)
        next_due = 0.0

        while not buffer.closed:
            frame = await anyio.to_thread.run_sync(buffer.await_latest, last_seq, 1.0)
            if frame is None:
                continue  # timeout: loop again (lets disconnects break out)
            last_seq = frame.seq

            now = time.monotonic()
            if now < next_due:
                continue  # client fps throttle: drop this frame
            next_due = now + min_interval

            jpeg = await anyio.to_thread.run_sync(
                self._encode, buffer, frame.seq, frame.image, transform, quality
            )

            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )
