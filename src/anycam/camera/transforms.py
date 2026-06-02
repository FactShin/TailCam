"""Pure, stateless frame transforms (numpy/cv2). Easy to unit test.

Per-camera transforms (rotate/flip) are applied once in the capture worker and
shared by all consumers. Per-stream transforms (zoom/pan/resize) are applied in
the streaming backend so each client is independent.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


def rotate(image: np.ndarray, degrees: int) -> np.ndarray:
    deg = degrees % 360
    if deg == 0:
        return image
    if deg == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"rotation must be a multiple of 90, got {degrees}")


def flip(image: np.ndarray, horizontal: bool = False, vertical: bool = False) -> np.ndarray:
    if horizontal and vertical:
        return cv2.flip(image, -1)
    if horizontal:
        return cv2.flip(image, 1)
    if vertical:
        return cv2.flip(image, 0)
    return image


def crop_zoom_pan(
    image: np.ndarray, zoom: float, pan_x: float = 0.5, pan_y: float = 0.5
) -> np.ndarray:
    """Digital zoom by cropping a centered window then scaling back up.

    ``zoom`` >= 1.0 (1.0 is no zoom). ``pan_x``/``pan_y`` in [0, 1] choose the
    center of the crop window. The output keeps the input dimensions.
    """
    if zoom <= 1.0:
        return image
    h, w = image.shape[:2]
    crop_w = max(1, int(round(w / zoom)))
    crop_h = max(1, int(round(h / zoom)))
    pan_x = min(1.0, max(0.0, pan_x))
    pan_y = min(1.0, max(0.0, pan_y))
    cx = int(round(pan_x * w))
    cy = int(round(pan_y * h))
    x0 = min(max(0, cx - crop_w // 2), w - crop_w)
    y0 = min(max(0, cy - crop_h // 2), h - crop_h)
    cropped = image[y0 : y0 + crop_h, x0 : x0 + crop_w]
    return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)


def resize_max_width(image: np.ndarray, max_width: int) -> np.ndarray:
    if max_width <= 0:
        return image
    h, w = image.shape[:2]
    if w <= max_width:
        return image
    new_h = max(1, int(round(h * max_width / w)))
    return cv2.resize(image, (max_width, new_h), interpolation=cv2.INTER_AREA)


@dataclass
class CameraTransform:
    """Per-camera transform applied once in the capture worker."""

    rotation: int = 0
    flip_h: bool = False
    flip_v: bool = False

    def apply(self, image: np.ndarray) -> np.ndarray:
        out = rotate(image, self.rotation)
        out = flip(out, horizontal=self.flip_h, vertical=self.flip_v)
        return out

    def is_identity(self) -> bool:
        return self.rotation % 360 == 0 and not self.flip_h and not self.flip_v


@dataclass(frozen=True)
class StreamTransform:
    """Per-stream transform applied in the streaming backend."""

    zoom: float = 1.0
    pan_x: float = 0.5
    pan_y: float = 0.5
    max_width: int = 0

    def apply(self, image: np.ndarray) -> np.ndarray:
        out = crop_zoom_pan(image, self.zoom, self.pan_x, self.pan_y)
        out = resize_max_width(out, self.max_width)
        return out
