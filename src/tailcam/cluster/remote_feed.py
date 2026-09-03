"""Pull a peer's camera over its MJPEG stream into a local ``FrameBuffer``.

This is how a *storage node* records, timelapses, and analyzes a camera that is
physically attached to another TailCam node (say, a Raspberry Pi): the Pi keeps
doing the one cheap thing it's good at — capturing and serving MJPEG — while the
bigger box pulls that stream over the tailnet and runs the recorder, the
timelapse worker, and the models against a normal frame buffer. Nothing
downstream knows the camera is remote.

A feed lives while something reads it and shuts itself down after ~30 s of
nobody waiting on the buffer, so an idle storage node holds no streams open.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import numpy as np

from tailcam.camera.frame import FrameBuffer
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_IDLE_TIMEOUT = 30.0
_RECONNECT_MAX = 30.0
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_MAX_PART = 16 * 1024 * 1024  # a JPEG frame larger than this is garbage


def iter_mjpeg_frames(chunks: Iterator[bytes]) -> Iterator[bytes]:
    """Yield complete JPEG payloads from an MJPEG byte stream.

    Boundary/header handling is deliberately loose: we scan for the JPEG
    SOI/EOI markers, which is what every browser does, so any server's
    multipart framing works (including TailCam's own).
    """
    buf = bytearray()
    for chunk in chunks:
        if not chunk:
            continue
        buf.extend(chunk)
        while True:
            start = buf.find(_SOI)
            if start < 0:
                # No frame start in the buffer; keep only the tail in case a
                # marker straddles two chunks.
                if len(buf) > 1:
                    del buf[:-1]
                break
            end = buf.find(_EOI, start + 2)
            if end < 0:
                if start > 0:
                    del buf[:start]
                if len(buf) > _MAX_PART:
                    buf.clear()
                break
            yield bytes(buf[start : end + 2])
            del buf[: end + 2]


class RemoteFeed:
    """One pulled stream → one FrameBuffer, with reconnect + idle shutdown."""

    def __init__(
        self,
        key: str,
        url: str,
        client_factory: Callable[[], object] | None = None,
    ) -> None:
        self.key = key
        self.url = url
        self.buffer = FrameBuffer()
        self._client_factory = client_factory
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"remote-feed-{key}", daemon=True)
        self.last_error = ""
        self.frames = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.buffer.close()
        self._thread.join(timeout=5.0)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive() and not self.buffer.closed

    def _idle(self) -> bool:
        last = self.buffer.last_wait_at
        return last > 0 and (time.monotonic() - last) > _IDLE_TIMEOUT

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        import httpx

        # read=None: MJPEG is open-ended; connect/write stay bounded.
        return httpx.Client(timeout=httpx.Timeout(5.0, read=None), follow_redirects=False)

    def _run(self) -> None:
        import cv2

        backoff = 1.0
        while not self._stop.is_set():
            try:
                client = self._client()
                with client, client.stream("GET", self.url) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"HTTP {resp.status_code}")
                    backoff = 1.0
                    self.last_error = ""
                    for jpeg in iter_mjpeg_frames(resp.iter_bytes(chunk_size=1 << 15)):
                        if self._stop.is_set():
                            break
                        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if image is None:
                            continue
                        self.buffer.publish(image)
                        self.frames += 1
                        if self._idle():
                            log.info("remote feed %s idle; closing", self.key)
                            self._stop.set()
                            break
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.last_error = str(exc)
                log.warning("remote feed %s: %s (retry in %.0fs)", self.key, exc, backoff)
            if self._stop.is_set():
                break
            if self._idle():
                log.info("remote feed %s idle; closing", self.key)
                break
            if self._stop.wait(backoff):
                break
            backoff = min(_RECONNECT_MAX, backoff * 2)
        self.buffer.close()


class RemoteFeedRegistry:
    """Lazily-started feeds keyed by ``<peer key>|<camera id>``.

    ``resolve_base`` maps a peer key to its base URL (``None`` when the peer is
    unknown). ``get_buffer`` follows the ``CameraManager.get_buffer`` contract
    so recorders/timelapse workers can use it as their ``reacquire`` callable.
    """

    def __init__(self, resolve_base: Callable[[str], str | None]) -> None:
        self._resolve_base = resolve_base
        self._feeds: dict[str, RemoteFeed] = {}
        self._lock = threading.Lock()

    @staticmethod
    def feed_key(source_key: str, camera_id: str) -> str:
        return f"{source_key}|{camera_id}"

    def stream_url(self, base: str, camera_id: str, fps: int | None = None) -> str:
        from urllib.parse import quote

        url = f"{base}/stream/{quote(camera_id, safe='/')}.mjpg"
        return f"{url}?fps={int(fps)}" if fps else url

    def get_buffer(
        self, source_key: str, camera_id: str, fps: int | None = None
    ) -> FrameBuffer | None:
        """The live buffer for a peer's camera (starting the pull if needed)."""
        base = self._resolve_base(source_key)
        if base is None:
            return None
        key = self.feed_key(source_key, camera_id)
        with self._lock:
            feed = self._feeds.get(key)
            if feed is None or not feed.alive:
                feed = RemoteFeed(key, self.stream_url(base, camera_id, fps))
                self._feeds[key] = feed
                feed.start()
                log.info("remote feed %s started from %s", key, base)
            return feed.buffer

    def status(self) -> list[dict]:
        with self._lock:
            return [
                {"key": k, "alive": f.alive, "frames": f.frames, "error": f.last_error}
                for k, f in self._feeds.items()
            ]

    def shutdown(self) -> None:
        with self._lock:
            feeds = list(self._feeds.values())
            self._feeds.clear()
        for feed in feeds:
            feed.stop()
