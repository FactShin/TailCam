"""JPEG encoding. Central place to later swap in PyTurboJPEG."""

from __future__ import annotations

import cv2
import numpy as np


def encode_jpeg(image: np.ndarray, quality: int = 80) -> bytes:
    quality = max(1, min(100, int(quality)))
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()
