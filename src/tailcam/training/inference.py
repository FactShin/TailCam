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

import numpy as np

from tailcam.ai.analyzer import Analysis, Detection, OllamaAnalyzer
from tailcam.ai.detector import BuiltinDetector
from tailcam.config import TrainingConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.store import Store

__all__ = ["Detection", "InferenceRouter", "LocalClassifier", "LocalDetector"]

log = get_logger(__name__)


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
    """Duck-types as a FrameAnalyzer. Priority: the user's trained/BYO model,
    then Ollama (if the user enabled it), then the zero-config built-in
    detector — so labels and boxes work out of the box and get *better* as the
    user opts into more."""

    def __init__(
        self,
        store: Store,
        config: TrainingConfig,
        ollama: OllamaAnalyzer,
        builtin: BuiltinDetector | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._ollama = ollama
        self._builtin = builtin
        self._lock = threading.Lock()
        self._cached_id: int | None = None
        self._classifier: LocalClassifier | None = None
        self._detector: LocalDetector | None = None
        self._active_name: str = ""
        self._load_error: str = ""

    def _refresh_active(self) -> None:
        """Load (and cache) the active model as a classifier or detector, by task.
        Caller holds ``self._lock``. Records why a model ISN'T running in
        ``_load_error`` so the UI can say so instead of silently falling back."""
        mid = self._config.active_model_id
        if self._cached_id == mid:
            return
        self._cached_id = mid
        self._classifier = None
        self._detector = None
        self._active_name = ""
        self._load_error = ""
        if not mid:
            return
        record = self._store.get_model(mid)
        if record is None:
            self._load_error = f"active model #{mid} no longer exists"
            return
        self._active_name = record.name
        if not record.path:
            self._load_error = (
                "this model has no weights yet — it downloads on first training run"
            )
            return
        if record.task == "detection":
            det = LocalDetector(record.path, self._config.detect_conf)
            if det.load():
                self._detector = det
            else:
                self._load_error = "model failed to load (is the training engine installed?)"
            return
        try:
            classes = json.loads(record.classes_json) or []
        except (ValueError, TypeError):
            classes = []
        clf = LocalClassifier(record.path, classes)
        if clf.load():
            self._classifier = clf
        else:
            self._load_error = "model failed to load (is the training engine installed?)"

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
        return local or self._ollama.enabled or (
            self._builtin is not None and self._builtin.enabled
        )

    @property
    def detection_active(self) -> bool:
        """True when something produces bounding boxes (for the UI overlay) —
        a trained detection model, or the built-in detector."""
        if self._active_detector() is not None:
            return True
        return self._builtin is not None and self._builtin.enabled

    def detection_note(self) -> str:
        """Status line for the overlay badge while the built-in detector is
        provisioning itself ("downloading model 42%") or failing."""
        if self._active_detector() is not None or self._builtin is None:
            return ""
        s = self._builtin.status()
        if s.status == "downloading":
            pct = f" {s.percent:.0f}%" if s.percent else ""
            return (s.detail or "downloading model") + pct
        if s.status == "error":
            return f"detector error: {s.error}"
        return ""

    def describe(self) -> dict:
        """The truth about what analyzes frames right now (for /api/ai).

        ``mode`` is one of: ``local`` (a trained/BYO model is loaded), ``ollama``
        (falling back to / using the Ollama analyzer), or ``off``. When a local
        model was selected but isn't running, ``error`` says why.
        """
        with self._lock:
            self._refresh_active()
            clf, det = self._classifier, self._detector
            name, err = self._active_name, self._load_error
        if clf is not None or det is not None:
            return {
                "mode": "local",
                "model_name": name,
                "task": "detection" if det is not None else "classification",
                "error": "",
            }
        if self._ollama.enabled:
            return {
                "mode": "ollama",
                "model_name": self._ollama.config.model,
                "task": "classification",
                "error": err,  # e.g. selected local model failed -> tell the user
            }
        if self._builtin is not None and self._builtin.enabled:
            s = self._builtin.status()
            return {
                "mode": "builtin",
                "model_name": s.model,
                "task": "detection",
                "error": err or s.error,
            }
        return {"mode": "off", "model_name": "", "task": "", "error": err}

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
        # Zero-config fallback: label motion events with the built-in detector's
        # best box, so event badges (PERSON, CUP, DOG …) work with no setup.
        if self._builtin is not None and self._builtin.enabled:
            detections = self._builtin.detect(image)
            if detections:
                top = max(detections, key=lambda d: d.confidence)
                others = {d.label for d in detections if d.label != top.label}
                extra = f" (+ {', '.join(sorted(others))})" if others else ""
                return Analysis(
                    label=top.label,
                    description=f"{top.label} ({top.confidence:.0%}){extra}",
                    confidence=top.confidence,
                )
            if self._builtin.ready:  # ran cleanly, just saw nothing
                return Analysis(label="nothing", description="no objects", confidence=0.0)
        return None

    def detect(self, image: np.ndarray) -> list[Detection] | None:
        """Bounding boxes from the active detection model, else the built-in
        detector. Returns None only when no box source exists at all (the live
        overlay treats that as 'detection unavailable')."""
        det = self._active_detector()
        if det is not None:
            return det.detect(image)
        if self._builtin is not None and self._builtin.enabled:
            return self._builtin.detect(image)
        return None
