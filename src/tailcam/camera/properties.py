"""Logical camera properties and their mapping to OpenCV ``CAP_PROP_*`` ids.

Property writes must only happen on the capture thread (the thread that owns the
``cv2.VideoCapture`` object), so callers submit changes through the
``CameraWorker`` command queue rather than touching the device directly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

try:  # OpenCV is optional at import time for unit tests that mock it
    import cv2

    LOGICAL_TO_CAP: dict[str, int] = {
        "width": cv2.CAP_PROP_FRAME_WIDTH,
        "height": cv2.CAP_PROP_FRAME_HEIGHT,
        "fps": cv2.CAP_PROP_FPS,
        "brightness": cv2.CAP_PROP_BRIGHTNESS,
        "contrast": cv2.CAP_PROP_CONTRAST,
        "saturation": cv2.CAP_PROP_SATURATION,
    }
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]
    LOGICAL_TO_CAP = {}


@dataclass
class CameraProperties:
    """Hardware-level capture properties applied to the device."""

    width: int = 1280
    height: int = 720
    fps: int = 30
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CameraProperties:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
