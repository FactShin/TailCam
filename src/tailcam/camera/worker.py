"""Per-camera capture thread.

Owns a ``CameraSource`` and publishes the latest frame to a ``FrameBuffer``.
All device mutations (property/transform changes, reconnect) happen on this
thread via a command queue, so the ``cv2.VideoCapture`` object is never touched
from request threads.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from tailcam.camera.frame import FrameBuffer
from tailcam.camera.properties import CameraProperties
from tailcam.camera.source import CameraDescriptor, CameraSource, create_source
from tailcam.camera.transforms import CameraTransform
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_MAX_CONSECUTIVE_FAILURES = 10
# Cap the retry interval: a permanently failing camera (unplugged USB, an
# out-of-reach iPhone Continuity Camera, denied permission) must not hammer
# reopen attempts, but a replugged webcam should be back within seconds, not
# a minute. An open attempt on a missing device is a cheap failure.
_RECONNECT_BACKOFF_MAX = 15.0
# With nobody waiting on the buffer for this long, the loop only grab()s
# (keeps the device streaming, no JPEG decode) — the biggest idle CPU saving
# on a Pi with cameras nobody is watching.
_IDLE_GRAB_AFTER = 3.0

SourceFactory = Callable[[CameraDescriptor, CameraProperties], CameraSource]


class CameraStatus(str, Enum):
    OFFLINE = "offline"
    ONLINE = "online"
    DEGRADED = "degraded"


@dataclass
class CameraState:
    status: CameraStatus = CameraStatus.OFFLINE
    fps: float = 0.0
    last_error: str | None = None
    properties: CameraProperties = field(default_factory=CameraProperties)
    transform: CameraTransform = field(default_factory=CameraTransform)


class CameraWorker:
    def __init__(
        self,
        descriptor: CameraDescriptor,
        properties: CameraProperties | None = None,
        transform: CameraTransform | None = None,
        source_factory: SourceFactory = create_source,
    ) -> None:
        self.descriptor = descriptor
        self.buffer = FrameBuffer()
        self.state = CameraState(
            properties=properties or CameraProperties(),
            transform=transform or CameraTransform(),
        )
        self._source_factory = source_factory
        self._commands: queue.Queue[Callable[[CameraSource], None]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frame_times: list[float] = []

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"camera-{self.descriptor.id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.buffer.close()
        thread = self._thread
        if thread:
            thread.join(timeout=5.0)
            if thread.is_alive():
                # Still inside a blocking driver read: keep the reference so
                # running stays truthful (a replacement would only get EBUSY
                # until this thread lets go of the device).
                log.warning("camera %s: capture thread slow to exit", self.descriptor.id)
                return
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- commands ----------------------------------------------------------
    def set_property(self, name: str, value: float) -> None:
        setattr(self.state.properties, name, value)
        self._commands.put(lambda src: src.set_property(name, value))

    def set_transform(self, transform: CameraTransform) -> None:
        self.state.transform = transform

    # -- capture loop ------------------------------------------------------
    def _drain_commands(self, source: CameraSource) -> None:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                cmd(source)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Camera command failed on %s: %s", self.descriptor.id, exc)

    def _record_fps(self) -> None:
        now = time.monotonic()
        self._frame_times.append(now)
        cutoff = now - 1.0
        self._frame_times = [t for t in self._frame_times if t >= cutoff]
        self.state.fps = float(len(self._frame_times))

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            source = self._source_factory(self.descriptor, self.state.properties)
            if not source.open():
                self.state.status = CameraStatus.OFFLINE
                if sys.platform == "darwin":
                    self.state.last_error = (
                        "can't open device — if this persists, grant camera access in "
                        "System Settings › Privacy & Security › Camera"
                    )
                elif sys.platform == "win32":
                    self.state.last_error = (
                        "no frames from device — close other apps using the camera, and "
                        "check Windows Settings › Privacy & security › Camera › "
                        "“Let desktop apps access your camera”"
                    )
                else:
                    self.state.last_error = (
                        getattr(source, "last_error", "")
                        or "can't open device (in use, unplugged, or denied)"
                    )
                source.close()
                if self._stop.wait(backoff):
                    break
                backoff = min(_RECONNECT_BACKOFF_MAX, backoff * 2)
                continue

            backoff = 0.5
            self.state.status = CameraStatus.ONLINE
            # A downgraded open (e.g. 720p refused, running at 480p) keeps its
            # note so the camera page can say why the picture is smaller.
            self.state.last_error = getattr(source, "last_error", "") or None
            failures = 0
            transform = self.state.transform

            try:
                while not self._stop.is_set():
                    self._drain_commands(source)
                    if self.buffer.idle_for() > _IDLE_GRAB_AFTER:
                        # Nobody is watching/recording/analyzing: pull frames
                        # off the device without decoding them.
                        if source.grab():
                            failures = 0
                            self._record_fps()
                        else:
                            failures += 1
                            if failures >= _MAX_CONSECUTIVE_FAILURES:
                                self.state.status = CameraStatus.DEGRADED
                                self.state.last_error = "read failures; reconnecting"
                                break
                            time.sleep(0.05)
                        continue
                    image = source.read()
                    if image is None:
                        failures += 1
                        if failures >= _MAX_CONSECUTIVE_FAILURES:
                            self.state.status = CameraStatus.DEGRADED
                            self.state.last_error = "read failures; reconnecting"
                            break
                        time.sleep(0.05)
                        continue
                    failures = 0
                    transform = self.state.transform
                    if not transform.is_identity():
                        image = transform.apply(image)
                    self.buffer.publish(image)
                    self._record_fps()
            finally:
                source.close()
        self.state.status = CameraStatus.OFFLINE
