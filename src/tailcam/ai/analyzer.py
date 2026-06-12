"""Ollama vision-model analyzer.

Sends a single JPEG frame to Ollama's /api/generate with format=json and parses
a constrained answer. Pure-stdlib + httpx; no Ollama SDK dependency.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import cv2
import httpx
import numpy as np

from tailcam.config import AIConfig
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_VALID_LABELS = {"person", "animal", "vehicle", "package", "plant", "nothing"}


@dataclass
class Analysis:
    label: str
    description: str
    confidence: float


def _coerce(data: dict) -> Analysis | None:
    """Normalize the model's JSON into a safe Analysis, or None if unusable."""
    label = str(data.get("label", "")).strip().lower()
    if label not in _VALID_LABELS:
        # Models sometimes answer e.g. "a person" — take the first known word.
        label = next((w for w in label.replace(",", " ").split() if w in _VALID_LABELS), "")
    if not label:
        return None
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    desc = str(data.get("description", "")).strip()[:200]
    return Analysis(label=label, description=desc, confidence=conf)


class OllamaAnalyzer:
    def __init__(self, config: AIConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _encode(self, image: np.ndarray) -> str:
        # Downscale large frames — the model doesn't need full res and it keeps
        # the request small/fast.
        h, w = image.shape[:2]
        if w > 768:
            image = cv2.resize(image, (768, max(1, int(h * 768 / w))))
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(buf.tobytes()).decode() if ok else ""

    def analyze(self, image: np.ndarray) -> Analysis | None:
        """Blocking; call from a worker thread. Returns None on any failure."""
        if not self.config.enabled:
            return None
        b64 = self._encode(image)
        if not b64:
            return None
        payload = {
            "model": self.config.model,
            "prompt": self.config.prompt,
            "images": [b64],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        try:
            r = httpx.post(
                f"{self.config.base_url.rstrip('/')}/api/generate",
                json=payload,
                timeout=self.config.timeout,
            )
            r.raise_for_status()
            response_text = r.json().get("response", "")
            return _coerce(json.loads(response_text))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.debug("Ollama analyze failed: %s", exc)
            return None

    def health(self) -> tuple[bool, str | None]:
        """(reachable, model_present) for diagnostics. Never raises."""
        if not self.config.enabled:
            return (False, None)
        try:
            r = httpx.get(f"{self.config.base_url.rstrip('/')}/api/tags", timeout=4.0)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            want = self.config.model
            present = any(m == want or m.startswith(want + ":") for m in models)
            return (True, want if present else None)
        except (httpx.HTTPError, ValueError):
            return (False, None)
