"""MJPEG (multipart/x-mixed-replace) streaming backend.

The frame wait and JPEG encode both run off the event loop (in a worker thread)
so a busy camera or expensive encode never blocks FastAPI's async loop. The
latest-only ``FrameBuffer`` means slow clients simply drop frames.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import anyio

from anycam.camera.frame import FrameBuffer
from anycam.camera.transforms import StreamTransform
from anycam.streaming.backend import StreamBackend
from anycam.streaming.encoder import encode_jpeg

BOUNDARY = "frame"


class MJPEGBackend(StreamBackend):
    media_type = f"multipart/x-mixed-replace; boundary={BOUNDARY}"

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

            image = frame.image
            if transform != StreamTransform():
                image = transform.apply(image)
            jpeg = await anyio.to_thread.run_sync(encode_jpeg, image, quality)

            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )
