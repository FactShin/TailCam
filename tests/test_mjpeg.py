"""Tests for the MJPEG streaming backend.

We drive the backend generator directly over a FrameBuffer rather than through
the HTTP stack: an infinite multipart stream cannot be cleanly closed by the
synchronous TestClient (httpx tries to drain the body), so a unit test of the
generator is both faster and deterministic. Route wiring is covered separately
by the single-frame snapshot endpoint in test_api.py.
"""

import threading

import anyio
import numpy as np

from tailcam.camera.frame import FrameBuffer
from tailcam.camera.transforms import StreamTransform
from tailcam.streaming.mjpeg import MJPEGBackend


def _producer(buffer: FrameBuffer, stop: threading.Event):
    while not stop.is_set():
        buffer.publish(np.zeros((48, 64, 3), dtype=np.uint8))
        stop.wait(0.02)


def test_backend_media_type():
    assert "multipart/x-mixed-replace" in MJPEGBackend().media_type


def test_stream_yields_jpeg_frames():
    buffer = FrameBuffer()
    stop = threading.Event()
    producer = threading.Thread(target=_producer, args=(buffer, stop), daemon=True)
    producer.start()

    async def collect() -> bytes:
        backend = MJPEGBackend()
        gen = backend.stream(buffer, StreamTransform(), target_fps=30, quality=80)
        data = b""
        try:
            async for chunk in gen:
                data += chunk
                if data.count(b"--frame") >= 2:
                    break
        finally:
            await gen.aclose()
        return data

    try:
        data = anyio.run(collect)
    finally:
        stop.set()
        producer.join(timeout=2.0)
        buffer.close()

    assert b"Content-Type: image/jpeg" in data
    assert b"\xff\xd8" in data  # JPEG start-of-image marker
    assert data.count(b"--frame") >= 2


def test_stream_stops_when_buffer_closed():
    buffer = FrameBuffer()
    buffer.publish(np.zeros((8, 8, 3), dtype=np.uint8))

    async def drain() -> int:
        backend = MJPEGBackend()
        gen = backend.stream(buffer, StreamTransform(), target_fps=30, quality=80)
        count = 0
        with anyio.fail_after(5):
            async for _chunk in gen:
                count += 1
                if count == 1:
                    buffer.close()  # generator should exit on next loop check
        return count

    produced = anyio.run(drain)
    assert produced >= 1
