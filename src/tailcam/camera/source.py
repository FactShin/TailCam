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


class OpenCVCameraSource(CameraSource):
    def __init__(self, descriptor: CameraDescriptor, props: CameraProperties) -> None:
        self.descriptor = descriptor
        self.props = props
        self._cap: Any = None

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

    def open(self) -> bool:
        import cv2

        self._cap = cv2.VideoCapture(self._device_arg(), self._api_preference())
        if not self._cap.isOpened() and sys.platform == "win32":
            # Some Windows drivers/OpenCV builds reject DirectShow by-index
            # capture; Media Foundation usually works for the same index.
            self._cap.release()
            log.info("DSHOW open failed for %s; retrying with MSMF", self.descriptor.id)
            self._cap = cv2.VideoCapture(self._device_arg(), cv2.CAP_MSMF)
        if not self._cap.isOpened():
            log.warning("Failed to open camera %s", self.descriptor.id)
            return False
        # Apply initial properties.
        for name in ("width", "height", "fps", "brightness", "contrast", "saturation"):
            value = getattr(self.props, name, None)
            if value is not None:
                self.set_property(name, float(value))
        return True

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


def use_synthetic() -> bool:
    return os.environ.get("TAILCAM_SYNTHETIC") == "1"


def create_source(descriptor: CameraDescriptor, props: CameraProperties) -> CameraSource:
    if descriptor.backend == "synthetic" or use_synthetic():
        return SyntheticCameraSource(descriptor, props)
    return OpenCVCameraSource(descriptor, props)
