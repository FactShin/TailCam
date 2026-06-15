"""Route motion-event analysis to the active model.

If the user has activated a trained / bring-your-own model (and the engine is
installed to load it), it labels frames; otherwise we fall back to the Ollama
analyzer. This is the payoff of training: "use our model or your own."
"""

from __future__ import annotations

import json
import threading

import numpy as np

from tailcam.ai.analyzer import Analysis, OllamaAnalyzer
from tailcam.config import TrainingConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.store import Store

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


class InferenceRouter:
    """Duck-types as a FrameAnalyzer: prefers the active local model, else Ollama."""

    def __init__(self, store: Store, config: TrainingConfig, ollama: OllamaAnalyzer) -> None:
        self._store = store
        self._config = config
        self._ollama = ollama
        self._lock = threading.Lock()
        self._cached_id: int | None = None
        self._classifier: LocalClassifier | None = None

    def _active_classifier(self) -> LocalClassifier | None:
        mid = self._config.active_model_id
        if not mid:
            return None
        with self._lock:
            if self._cached_id == mid:
                return self._classifier
            self._cached_id = mid
            self._classifier = None
            record = self._store.get_model(mid)
            if record is None or not record.path:
                return None
            try:
                classes = json.loads(record.classes_json) or []
            except (ValueError, TypeError):
                classes = []
            clf = LocalClassifier(record.path, classes)
            self._classifier = clf if clf.load() else None
            return self._classifier

    @property
    def enabled(self) -> bool:
        return self._active_classifier() is not None or self._ollama.enabled

    def analyze(self, image: np.ndarray) -> Analysis | None:
        clf = self._active_classifier()
        if clf is not None:
            result = clf.analyze(image)
            if result is not None:
                return result
            # local model failed mid-run — fall through to Ollama if available
        if self._ollama.enabled:
            return self._ollama.analyze(image)
        return None
