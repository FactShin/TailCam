"""Camera source abstraction. The only place that touches OpenCV capture.

A ``SyntheticCameraSource`` lets the whole stack run headless (CI, dev
containers) where no physical webcam exists.
"""

from __future__ import annotations

import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from tailcam.camera.properties import LOGICAL_TO_CAP, CameraProperties
from tailcam.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class CameraDescriptor:
    """Stable, persistable identity for a camera device."""

    id: str  # Linux: /dev/videoN path; macOS: index string; synthetic: "synthetic*"
    name: str
    backend: str  # "v4l2" | "avfoundation" | "synthetic"


class CameraSource(ABC):
    @abstractmethod
    def open(self) -> bool: ...

    @abstractmethod
    def read(self) -> np.ndarray | None: ...

    @abstractmethod
    def set_property(self, name: str, value: float) -> None: ...

    @abstractmethod
    def get_property(self, name: str) -> float: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...


# Windows: which capture API actually delivered frames for a given device, so
# reconnects skip straight to it instead of re-walking the ladder.
_WIN_BACKEND_CACHE: dict[str, int] = {}


class OpenCVCameraSource(CameraSource):
    def __init__(self, descriptor: CameraDescriptor, props: CameraProperties) -> None:
        self.descriptor = descriptor
        self.props = props
        self._cap: Any = None
        # Why the last open() failed (shown on the camera page), and what the
        # driver actually gave us on success: (fourcc, width, height).
        self.last_error = ""
        self.negotiated: tuple[str, int, int] | None = None

    def _api_preference(self) -> int:
        import cv2

        if sys.platform == "darwin":
            return cv2.CAP_AVFOUNDATION
        if sys.platform == "win32":
            return cv2.CAP_DSHOW  # DirectShow: reliable enumeration + capture on Windows
        return cv2.CAP_V4L2

    def _device_arg(self):
        # Linux opens device paths directly; macOS (avfoundation) and Windows
        # (dshow) use an integer index.
        if self.descriptor.backend in ("avfoundation", "dshow"):
            return int(self.descriptor.id)
        return self.descriptor.id

    def _verify_frames(self, cap: Any, deadline_s: float = 3.0) -> bool:
        """True once the capture delivers a real frame.

        On Windows, ``isOpened()`` is not proof of a working camera: DirectShow
        happily "opens" a device (or an IR/virtual node) and then never returns
        a frame. Treat frame delivery, not open, as success.
        """
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            ok, frame = cap.read()
            if ok and frame is not None and getattr(frame, "size", 0) > 0:
                return True
            time.sleep(0.1)
        return False

    def _open_windows(self) -> bool:
        """Walk capture backends until one actually produces frames."""
        import cv2

        ladder = [
            (cv2.CAP_DSHOW, "DSHOW"),
            (cv2.CAP_MSMF, "MSMF"),
            (cv2.CAP_ANY, "ANY"),
        ]
        cached = _WIN_BACKEND_CACHE.get(self.descriptor.id)
        if cached is not None:
            ladder.sort(key=lambda item: item[0] != cached)
        for api, name in ladder:
            cap = cv2.VideoCapture(self._device_arg(), api)
            if not cap.isOpened():
                cap.release()
                continue
            if not self._verify_frames(cap):
                log.info(
                    "Camera %s opened via %s but delivered no frames; trying next backend",
                    self.descriptor.id, name,
                )
                cap.release()
                continue
            log.info("Camera %s capturing via %s", self.descriptor.id, name)
            _WIN_BACKEND_CACHE[self.descriptor.id] = api
            self._cap = cap
            return True
        return False

    def open(self) -> bool:
        import cv2

        self.last_error = ""
        if sys.platform == "win32":
            if not self._open_windows():
                log.warning(
                    "Failed to open camera %s (no backend delivered frames)",
                    self.descriptor.id,
                )
                return False
        elif self.descriptor.backend == "v4l2":
            if not self._open_v4l2(cv2):
                return False
        else:
            self._cap = cv2.VideoCapture(self._device_arg(), self._api_preference())
            if not self._cap.isOpened():
                log.warning("Failed to open camera %s", self.descriptor.id)
                return False
        # Apply initial properties (size/fps were negotiated at open on V4L2).
        names: tuple[str, ...] = ("brightness", "contrast", "saturation")
        if self.descriptor.backend != "v4l2":
            names = ("width", "height", "fps", *names)
        for name in names:
            value = getattr(self.props, name, None)
            if value is not None:
                self.set_property(name, float(value))
        return True

    # V4L2 open ladder. Every attempt asks for the format BEFORE the first
    # STREAMON — that's the whole point: OpenCV's default open negotiates raw
    # YUYV at 640x480 and streams immediately, and on a Raspberry Pi with two
    # webcams on one USB 2.0 bus the second STREAMON fails with ENOSPC ("No
    # space left on device"), which surfaces as "can't open device". MJPEG at
    # the wanted size needs a tenth of the bandwidth; if even that is refused
    # we step down to 640x480 rather than leaving the camera offline.
    def _v4l2_attempts(self) -> list[tuple[str, int | None, int | None]]:
        w, h = int(self.props.width or 0) or None, int(self.props.height or 0) or None
        if os.environ.get("TAILCAM_RAW_V4L2") == "1":
            return [("", w, h), ("", None, None)]
        attempts: list[tuple[str, int | None, int | None]] = [("MJPG", w, h)]
        if (w, h) != (640, 480):
            attempts.append(("MJPG", 640, 480))
        attempts.append(("", w, h))  # raw, as before
        attempts.append(("", None, None))  # whatever the driver defaults to
        return attempts

    def _open_v4l2(self, cv2: Any) -> bool:
        wanted = (int(self.props.width or 0), int(self.props.height or 0))
        for fourcc, w, h in self._v4l2_attempts():
            params: list[int] = [cv2.CAP_PROP_BUFFERSIZE, 1]
            if fourcc:
                params += [cv2.CAP_PROP_FOURCC, int(cv2.VideoWriter_fourcc(*fourcc))]
            if w and h:
                params += [cv2.CAP_PROP_FRAME_WIDTH, w, cv2.CAP_PROP_FRAME_HEIGHT, h]
            if self.props.fps:
                params += [cv2.CAP_PROP_FPS, int(self.props.fps)]
            try:
                cap = cv2.VideoCapture(self._device_arg(), cv2.CAP_V4L2, params)
            except (cv2.error, TypeError):  # very old OpenCV without open params
                cap = cv2.VideoCapture(self._device_arg(), cv2.CAP_V4L2)
            if cap.isOpened() and self._verify_frames(cap, deadline_s=2.5):
                self._cap = cap
                self._log_v4l2_format(cv2, fourcc or "raw", (w, h), wanted)
                return True
            cap.release()
        self.last_error = linux_open_diagnosis(self.descriptor.id)
        log.warning("Failed to open camera %s: %s", self.descriptor.id, self.last_error)
        return False

    def _log_v4l2_format(
        self, cv2: Any, asked: str, size: tuple[int | None, int | None], wanted: tuple[int, int]
    ) -> None:
        try:
            got = int(self._cap.get(cv2.CAP_PROP_FOURCC)) & 0xFFFFFFFF
            name = "".join(chr((got >> (8 * i)) & 0xFF) for i in range(4)).strip() or str(got)
            gw = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            gh = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        except Exception:  # pragma: no cover - driver quirks
            name, gw, gh = asked, 0, 0
        self.negotiated = (name, gw, gh)
        log.info("Camera %s pixel format: %s %sx%s", self.descriptor.id, name, gw, gh)
        if wanted[0] and (gw, gh) != wanted and gw:
            log.warning(
                "Camera %s refused %sx%s (USB bandwidth?) — running at %sx%s. Move it to "
                "another USB port or see `tailcam doctor` for the uvcvideo bandwidth fix.",
                self.descriptor.id, wanted[0], wanted[1], gw, gh,
            )
            self.last_error = (
                f"running at {gw}x{gh}: {wanted[0]}x{wanted[1]} refused (USB bandwidth?)"
            )

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame

    def set_property(self, name: str, value: float) -> None:
        if self._cap is None or name not in LOGICAL_TO_CAP:
            return
        self._cap.set(LOGICAL_TO_CAP[name], value)

    def get_property(self, name: str) -> float:
        if self._cap is None or name not in LOGICAL_TO_CAP:
            return 0.0
        return float(self._cap.get(LOGICAL_TO_CAP[name]))

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()


class SyntheticCameraSource(CameraSource):
    """Generates a deterministic moving pattern. Used when no device exists."""

    def __init__(self, descriptor: CameraDescriptor, props: CameraProperties) -> None:
        self.descriptor = descriptor
        self.props = props
        self._opened = False
        self._frame_index = 0
        self._start = time.time()

    def open(self) -> bool:
        self._opened = True
        return True

    def read(self) -> np.ndarray | None:
        if not self._opened:
            return None
        w, h = self.props.width, self.props.height
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # Gradient background that shifts over time for visible motion.
        shift = (self._frame_index * 4) % w
        xs = (np.arange(w) + shift) % w
        img[:, :, 0] = (xs * 255 // max(1, w)).astype(np.uint8)
        ys = np.arange(h)
        img[:, :, 1] = (ys[:, None] * 255 // max(1, h)).astype(np.uint8)
        # A moving white square so motion detection has something to find.
        box = max(20, w // 12)
        cx = int((w - box) * (0.5 + 0.5 * np.sin(self._frame_index / 15.0)))
        cy = int((h - box) * (0.5 + 0.5 * np.cos(self._frame_index / 23.0)))
        img[cy : cy + box, cx : cx + box] = 255
        self._frame_index += 1
        # Pace to the configured fps so consumers behave realistically.
        time.sleep(max(0.0, 1.0 / max(1, self.props.fps)))
        return img

    def set_property(self, name: str, value: float) -> None:
        if hasattr(self.props, name):
            setattr(self.props, name, type(getattr(self.props, name) or 0)(value))

    def get_property(self, name: str) -> float:
        return float(getattr(self.props, name, 0) or 0)

    def close(self) -> None:
        self._opened = False

    @property
    def is_open(self) -> bool:
        return self._opened


def linux_open_diagnosis(path: str) -> str:
    """Why a V4L2 node couldn't be opened for capture, in words a user can act
    on. Distinguishes the OS-level failures (unplugged, permission, busy) from
    the one that looks identical in OpenCV but isn't: the node opens fine and
    only *streaming* is refused — on a Raspberry Pi that is USB bandwidth."""
    import errno

    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except FileNotFoundError:
        return "device is gone (unplugged?) — it comes back automatically when reconnected"
    except PermissionError:
        return (
            "permission denied — add your user to the video group: "
            "sudo usermod -aG video $USER (then log out and back in)"
        )
    except OSError as exc:
        if exc.errno == errno.EBUSY:
            return "device is in use by another program"
        return f"can't open device ({exc.strerror or exc})"
    os.close(fd)
    return (
        "device opens but refuses to stream — usually USB bandwidth (two cameras on one "
        "USB 2.0 bus). Move a camera to another USB port, lower its resolution, or apply "
        "the uvcvideo bandwidth fix (`tailcam doctor`)."
    )


def uvc_bandwidth_quirk_active() -> bool:
    """Whether uvcvideo runs with UVC_QUIRK_FIX_BANDWIDTH (0x80) — the kernel
    setting that lets two USB webcams share a Raspberry Pi's USB bus."""
    try:
        raw = Path("/sys/module/uvcvideo/parameters/quirks").read_text().strip()
        return bool(int(raw, 0) & 0x80)
    except (OSError, ValueError):
        pass
    try:
        for conf in Path("/etc/modprobe.d").glob("*.conf"):
            for line in conf.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("options") and "uvcvideo" in line and "quirks" in line:
                    value = line.split("quirks=", 1)[1].split()[0]
                    return bool(int(value, 0) & 0x80)
    except (OSError, ValueError):
        pass
    return False


def use_synthetic() -> bool:
    return os.environ.get("TAILCAM_SYNTHETIC") == "1"


def create_source(descriptor: CameraDescriptor, props: CameraProperties) -> CameraSource:
    if descriptor.backend == "synthetic" or use_synthetic():
        return SyntheticCameraSource(descriptor, props)
    return OpenCVCameraSource(descriptor, props)
