"""Non-blocking local Ollama analysis for 3D-printer timelapse evidence frames."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import httpx
import numpy as np

from tailcam.config import AIConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.models import TimelapseAnalysisEventRecord
from tailcam.persistence.store import Store

log = get_logger(__name__)

_VALID_STATES = {"healthy", "possible_failure", "failure", "uncertain"}
_PROMPT = (
    "You are monitoring a 3D printer. Inspect this frame for spaghetti, a detached "
    "print, layer shift, or other visible print failure. Respond ONLY with JSON: "
    '{"state": one of [healthy, possible_failure, failure, uncertain], '
    '"confidence": a number 0-1, "description": a short factual phrase}.'
)


@dataclass(frozen=True)
class PrinterAnalysis:
    state: str
    confidence: float
    description: str


def coerce_printer_analysis(data: dict) -> PrinterAnalysis | None:
    """Normalize strict printer-health JSON, rejecting unknown states."""
    state = str(data.get("state", "")).strip().lower()
    if state not in _VALID_STATES:
        return None
    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    description = str(data.get("description", "")).strip()[:240]
    return PrinterAnalysis(state, confidence, description)


class PrinterAnalyzer:
    def __init__(self, config: AIConfig) -> None:
        self.config = config

    def analyze_path(self, path: Path) -> PrinterAnalysis | None:
        image = cv2.imread(str(path))
        return self.analyze(image) if image is not None else None

    def analyze(self, image: np.ndarray) -> PrinterAnalysis | None:
        if not self.config.enabled:
            return None
        h, w = image.shape[:2]
        if w > 1024:
            image = cv2.resize(image, (1024, max(1, int(h * 1024 / w))))
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        payload = {
            "model": self.config.model,
            "prompt": _PROMPT,
            "images": [base64.b64encode(encoded.tobytes()).decode()],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        try:
            response = httpx.post(
                f"{self.config.base_url.rstrip('/')}/api/generate",
                json=payload,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            return coerce_printer_analysis(json.loads(response.json().get("response", "")))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.debug("Printer analysis failed: %s", exc)
            return None


class _Analyzer(Protocol):
    def analyze_path(self, path: Path) -> PrinterAnalysis | None: ...


class TimelapseAnalysisQueue:
    """One daemon worker keeps model work and latency outside capture threads.

    Only the newest pending frame for each timelapse is retained. A slow local
    model therefore catches up to current evidence instead of analyzing an
    unbounded backlog of stale frames during a long print.
    """

    def __init__(self, store: Store, analyzer: _Analyzer) -> None:
        self._store = store
        self._analyzer = analyzer
        self._pending: OrderedDict[int, tuple[int, int, Path]] = OrderedDict()
        self._condition = threading.Condition()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="timelapse-printer-analysis", daemon=True
        )
        self._thread.start()

    def submit(self, timelapse_id: int, frame_number: int, evidence_path: Path) -> None:
        with self._condition:
            if self._closed:
                return
            self._pending[timelapse_id] = (timelapse_id, frame_number, evidence_path)
            self._pending.move_to_end(timelapse_id)
            self._condition.notify()

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: bool(self._pending) or self._closed)
                if not self._pending:
                    return
                _, item = self._pending.popitem(last=False)
            timelapse_id, frame_number, evidence_path = item
            try:
                result = self._analyzer.analyze_path(evidence_path)
            except Exception:
                log.exception("Unexpected printer-analysis failure")
                result = None
            if result is None:
                result = PrinterAnalysis(
                    "uncertain", 0.0, "Local printer analysis unavailable for this frame"
                )
            try:
                self._store.add_timelapse_analysis_event(
                    TimelapseAnalysisEventRecord(
                        id=None,
                        timelapse_id=timelapse_id,
                        frame_number=frame_number,
                        state=result.state,
                        confidence=result.confidence,
                        description=result.description,
                        evidence_path=str(evidence_path),
                        created_ts=time.time(),
                    )
                )
            except Exception as exc:
                # The timelapse may have been deleted while model inference was
                # running. Keep the shared worker alive for subsequent prints.
                log.warning("Could not persist timelapse analysis event: %s", exc)
