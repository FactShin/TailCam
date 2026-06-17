"""Route analysis to the active model.

If the user has activated a trained / bring-your-own model (and the engine is
installed to load it), it labels frames; otherwise we fall back to the Ollama
analyzer. This is the payoff of training: "use our model or your own."

Two model tasks are supported:

- **classification** — one label for the whole frame (``analyze``).
- **detection** — bounding boxes (where + what) via ``detect``; for motion
  analysis the highest-confidence box is collapsed into a single label so a
  detection model also drives the existing event pipeline.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import numpy as np

from tailcam.ai.analyzer import Analysis, OllamaAnalyzer
from tailcam.config import TrainingConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.store import Store

log = get_logger(__name__)


@dataclass
class Detection:
    """One detected object. Coordinates are normalized 0..1 in the same
    center/size layout as a stored annotation, so the UI overlays them directly."""

    label: str
    confidence: float
    cx: float
    cy: float
    w: float
    h: float


class LocalClassifier:
    """Lazy-loaded Ultralytics classification model."""

    def __init__(self, model_path: str, classes: list[str]) -> None:
        self.model_path = model_path
        self.classes = classes
        self._model = None

    def load(self) -> bool:
        try:
            from ultralytics import YOLO

            self._model = YOLO(self.model_path)
            return True
        except Exception as exc:
            log.warning("could not load model %s: %s", self.model_path, exc)
            return False

    def analyze(self, image: np.ndarray) -> Analysis | None:
        if self._model is None and not self.load():
            return None
        model = self._model
        if model is None:  # pragma: no cover - load() guaranteed it
            return None
        try:
            result = model.predict(image, verbose=False)[0]
            probs = result.probs
            idx = int(probs.top1)
            conf = float(probs.top1conf)
            names = getattr(result, "names", {}) or {}
            label = names.get(idx) or (self.classes[idx] if idx < len(self.classes) else str(idx))
            return Analysis(label=str(label), description=f"{label} ({conf:.0%})", confidence=conf)
        except Exception as exc:  # pragma: no cover - inference failure
            log.warning("local model inference failed: %s", exc)
            return None


class LocalDetector:
    """Lazy-loaded Ultralytics detection model returning bounding boxes."""

    def __init__(self, model_path: str, conf: float = 0.35) -> None:
        self.model_path = model_path
        self.conf = conf
        self._model = None

    def load(self) -> bool:
        try:
            from ultralytics import YOLO

            self._model = YOLO(self.model_path)
            return True
        except Exception as exc:
            log.warning("could not load detection model %s: %s", self.model_path, exc)
            return False

    def detect(self, image: np.ndarray) -> list[Detection] | None:
        if self._model is None and not self.load():
            return None
        model = self._model
        if model is None:  # pragma: no cover - load() guaranteed it
            return None
        try:
            result = model.predict(image, verbose=False, conf=self.conf)[0]
        except Exception as exc:  # pragma: no cover - inference failure
            log.warning("detection inference failed: %s", exc)
            return None
        return _boxes_to_detections(result)


def _boxes_to_detections(result) -> list[Detection]:
    """Convert an Ultralytics result into normalized center/size detections."""
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    names = getattr(result, "names", {}) or {}
    detections: list[Detection] = []
    try:
        xywhn = boxes.xywhn.tolist()
        confs = boxes.conf.tolist()
        clss = boxes.cls.tolist()
    except Exception:  # pragma: no cover - unexpected tensor shape
        return []
    for (cx, cy, w, h), conf, cls in zip(xywhn, confs, clss, strict=False):
        idx = int(cls)
        detections.append(
            Detection(
                label=str(names.get(idx, idx)),
                confidence=float(conf),
                cx=float(cx),
                cy=float(cy),
                w=float(w),
                h=float(h),
            )
        )
    return detections


class InferenceRouter:
    """Duck-types as a FrameAnalyzer: prefers the active local model, else Ollama."""

    def __init__(self, store: Store, config: TrainingConfig, ollama: OllamaAnalyzer) -> None:
        self._store = store
        self._config = config
        self._ollama = ollama
        self._lock = threading.Lock()
        self._cached_id: int | None = None
        self._classifier: LocalClassifier | None = None
        self._detector: LocalDetector | None = None

    def _refresh_active(self) -> None:
        """Load (and cache) the active model as a classifier or detector, by task.
        Caller holds ``self._lock``."""
        mid = self._config.active_model_id
        if self._cached_id == mid:
            return
        self._cached_id = mid
        self._classifier = None
        self._detector = None
        if not mid:
            return
        record = self._store.get_model(mid)
        if record is None or not record.path:
            return
        if record.task == "detection":
            det = LocalDetector(record.path, self._config.detect_conf)
            self._detector = det if det.load() else None
            return
        try:
            classes = json.loads(record.classes_json) or []
        except (ValueError, TypeError):
            classes = []
        clf = LocalClassifier(record.path, classes)
        self._classifier = clf if clf.load() else None

    def _active_classifier(self) -> LocalClassifier | None:
        with self._lock:
            self._refresh_active()
            return self._classifier

    def _active_detector(self) -> LocalDetector | None:
        with self._lock:
            self._refresh_active()
            return self._detector

    @property
    def enabled(self) -> bool:
        with self._lock:
            self._refresh_active()
            local = self._classifier is not None or self._detector is not None
        return local or self._ollama.enabled

    @property
    def detection_active(self) -> bool:
        """True when the active model produces bounding boxes (for the UI overlay)."""
        return self._active_detector() is not None

    def analyze(self, image: np.ndarray) -> Analysis | None:
        with self._lock:
            self._refresh_active()
            clf = self._classifier
            det = self._detector
        if clf is not None:
            result = clf.analyze(image)
            if result is not None:
                return result
            # local model failed mid-run — fall through to Ollama if available
        elif det is not None:
            detections = det.detect(image)
            if detections:
                top = max(detections, key=lambda d: d.confidence)
                return Analysis(
                    label=top.label,
                    description=f"{top.label} ({top.confidence:.0%})",
                    confidence=top.confidence,
                )
            if detections is not None:  # ran cleanly, just saw nothing
                return Analysis(label="nothing", description="no objects", confidence=0.0)
        if self._ollama.enabled:
            return self._ollama.analyze(image)
        return None

    def detect(self, image: np.ndarray) -> list[Detection] | None:
        """Run the active detection model. Returns None if no detection model is
        active (the live overlay treats that as 'detection unavailable')."""
        det = self._active_detector()
        if det is None:
            return None
        return det.detect(image)
