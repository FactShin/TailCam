"""Pure, deterministic motion detection via frame differencing.

No threads, no I/O — feed it frames and it returns a result. This makes it
trivial to unit test with canned frame pairs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class MotionResult:
    motion: bool
    score: float  # fraction of pixels that changed, 0..1
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)  # x, y, w, h


class MotionDetector:
    def __init__(self, sensitivity: int = 50, min_area: int = 800) -> None:
        # Higher sensitivity -> lower pixel-difference threshold.
        self.sensitivity = max(1, min(100, sensitivity))
        self.min_area = min_area
        self._prev_gray: np.ndarray | None = None

    @property
    def threshold(self) -> int:
        # sensitivity 1 -> 60, sensitivity 100 -> ~5
        return int(60 - (self.sensitivity / 100.0) * 55)

    def reset(self) -> None:
        self._prev_gray = None

    def _prepare(self, image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (21, 21), 0)

    def process(self, image: np.ndarray) -> MotionResult:
        gray = self._prepare(image)
        if self._prev_gray is None:
            self._prev_gray = gray
            return MotionResult(motion=False, score=0.0)

        delta = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray
        _, thresh = cv2.threshold(delta, self.threshold, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=2)  # type: ignore[call-overload]

        changed = int(np.count_nonzero(thresh))
        total = thresh.size
        score = changed / total if total else 0.0

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            if cv2.contourArea(contour) < self.min_area:
                continue
            boxes.append(tuple(int(v) for v in cv2.boundingRect(contour)))  # type: ignore[arg-type]

        return MotionResult(motion=bool(boxes), score=score, boxes=boxes)
