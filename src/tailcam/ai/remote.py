"""Run object detection / frame analysis on another TailCam node.

A Raspberry Pi streaming two cameras has no CPU to spare for a YOLO model, but
the Mac mini or GPU box on the same tailnet does. Set ``[detection] node`` to
that node (its peer key, hostname, or full base URL) and every detection or
motion-label request from this node ships one JPEG to the peer's
``POST /api/detect-image`` and gets boxes/labels back — the peer runs *its*
full pipeline (trained model → Ollama → built-in detector), so the powerful box
decides what it sees.

Frames are downscaled before upload (detection models work at 416–640 px), so a
request is ~50 KB over the tailnet. Any failure degrades to "no boxes" and is
throttled so a dead peer can't stall the caller.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from tailcam.ai.analyzer import Analysis, Detection
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_UPLOAD_MAX_WIDTH = 640
_UPLOAD_QUALITY = 80
_TIMEOUT = 6.0
_FAILURE_BACKOFF = 15.0  # seconds to stay quiet after an unreachable peer


def _to_jpeg(image: np.ndarray) -> bytes:
    import cv2

    h, w = image.shape[:2]
    if w > _UPLOAD_MAX_WIDTH:
        scale = _UPLOAD_MAX_WIDTH / float(w)
        image = cv2.resize(
            image, (_UPLOAD_MAX_WIDTH, max(1, int(h * scale))), interpolation=cv2.INTER_AREA
        )
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _UPLOAD_QUALITY])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


class RemoteDetector:
    """Detection + analysis backed by a peer's ``/api/detect-image``.

    ``resolve_base`` returns the peer's base URL (or None while unknown). It's a
    callable because peers are discovered asynchronously and can move."""

    def __init__(
        self,
        resolve_base: Callable[[], str | None],
        label: str = "",
        client: Any = None,
    ) -> None:
        self._resolve_base = resolve_base
        self.label = label
        self._client = client
        self._lock = threading.Lock()
        self._down_until = 0.0
        self.last_error = ""

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
        return self._client

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._down_until and self._resolve_base() is not None

    def status(self) -> dict[str, Any]:
        base = self._resolve_base()
        return {
            "node": self.label,
            "base_url": base or "",
            "reachable": base is not None and time.monotonic() >= self._down_until,
            "error": self.last_error,
        }

    def _post(self, image: np.ndarray, mode: str) -> dict[str, Any] | None:
        if time.monotonic() < self._down_until:
            return None
        base = self._resolve_base()
        if base is None:
            self.last_error = "detection node not found on the tailnet"
            return None
        try:
            payload = _to_jpeg(image)
            resp = self._http().post(
                f"{base}/api/detect-image",
                params={"mode": mode},
                content=payload,
                headers={"Content-Type": "image/jpeg"},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
            self.last_error = ""
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:
            with self._lock:
                self._down_until = time.monotonic() + _FAILURE_BACKOFF
                self.last_error = str(exc)
            log.warning("remote detection via %s failed: %s", base, exc)
            return None

    def detect(self, image: np.ndarray) -> list[Detection] | None:
        """Boxes from the peer, or None when the peer is unavailable."""
        data = self._post(image, "detect")
        if data is None:
            return None
        if not data.get("detector_active", True):
            return []
        out: list[Detection] = []
        for b in data.get("boxes") or []:
            try:
                out.append(
                    Detection(
                        label=str(b["label"]), confidence=float(b["confidence"]),
                        cx=float(b["cx"]), cy=float(b["cy"]), w=float(b["w"]), h=float(b["h"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def analyze(self, image: np.ndarray) -> Analysis | None:
        data = self._post(image, "analyze")
        if not data or not data.get("label"):
            return None
        try:
            return Analysis(
                label=str(data["label"]),
                description=str(data.get("description") or data["label"]),
                confidence=float(data.get("confidence") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def model_name(self) -> str:
        return f"via {self.label}" if self.label else "remote node"
