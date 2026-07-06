"""Labeling-model abstraction for the active learning pipeline.

Every model that can watch frames — the built-in YOLO detector, a trained/BYO
model from the registry, Ollama, Florence-2, Qwen2.5-VL — is wrapped in a
:class:`LabelingBackend` so the pipeline itself never cares which one runs.
Backends report their own availability (missing package, missing GPU, wrong
OS) as human-readable text instead of raising, so the UI can explain what's
supported on this machine.

Backend ids are stable strings persisted in config:

- ``builtin`` — the plug-and-play object detector.
- ``model:<id>`` — a trained/BYO *detection* model from the registry.
- ``ollama`` — the Ollama vision analyzer (whole-frame label, no boxes).
- ``florence2`` — Florence-2 open-vocabulary detection (see :mod:`florence`).
- ``qwen2.5-vl`` — Qwen2.5-VL detection via transformers (see :mod:`qwen`).

New models plug in by adding a backend class here (or its own module) and one
entry in :func:`list_labeling_backends` — the pipeline and UI pick it up.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from tailcam.ai.analyzer import Detection, OllamaAnalyzer
from tailcam.ai.detector import BuiltinDetector
from tailcam.logging_setup import get_logger
from tailcam.persistence.store import Store

log = get_logger(__name__)


@dataclass
class BackendInfo:
    """What the UI needs to render a model choice: can it run here, and if
    not, what would make it work."""

    id: str
    name: str
    kind: str  # "detector" | "vlm" | "classifier"
    available: bool
    detail: str = ""  # availability note ("ready", or what's missing)
    boxes: bool = True  # produces bounding boxes (vs whole-frame labels)


@dataclass
class FinetuneInfo:
    """One fine-tunable model target and whether this machine can train it."""

    id: str
    name: str
    available: bool
    detail: str = ""


class LabelingBackend(Protocol):
    """What the active learning loop needs from any labeling model."""

    def info(self) -> BackendInfo: ...

    def predict(self, image: np.ndarray) -> list[Detection] | None:
        """Detections for one frame, or None when inference failed."""
        ...


# -- existing TailCam models ---------------------------------------------------


class BuiltinBackend:
    """The zero-setup object detector (80 COCO classes)."""

    def __init__(self, detector: BuiltinDetector) -> None:
        self._detector = detector

    def info(self) -> BackendInfo:
        s = self._detector.status()
        detail = "ready" if s.status == "ready" else (s.detail or s.error or s.status)
        return BackendInfo(
            id="builtin",
            name=f"Built-in detector ({s.model or 'YOLO'})",
            kind="detector",
            available=s.status != "error",
            detail=detail,
        )

    def predict(self, image: np.ndarray) -> list[Detection] | None:
        self._detector.ensure_ready()
        return self._detector.detect(image)


class RegistryModelBackend:
    """A trained / bring-your-own detection model from the model registry."""

    def __init__(self, store: Store, model_id: int, min_conf: float = 0.05) -> None:
        self._store = store
        self.model_id = model_id
        self._min_conf = min_conf
        self._detector = None

    def info(self) -> BackendInfo:
        record = self._store.get_model(self.model_id)
        bid = f"model:{self.model_id}"
        if record is None:
            return BackendInfo(
                id=bid, name=f"model #{self.model_id}", kind="detector",
                available=False, detail="model no longer exists",
            )
        if record.task != "detection":
            return BackendInfo(
                id=bid, name=record.name, kind="detector", available=False,
                detail="classification model — pick a detection model for boxes",
            )
        if not record.path:
            return BackendInfo(
                id=bid, name=record.name, kind="detector", available=False,
                detail="no weights yet — train it first",
            )
        return BackendInfo(
            id=bid, name=record.name, kind="detector", available=True, detail="ready"
        )

    def predict(self, image: np.ndarray) -> list[Detection] | None:
        if self._detector is None:
            from tailcam.training.inference import LocalDetector

            record = self._store.get_model(self.model_id)
            if record is None or not record.path:
                return None
            # A deliberately low floor so *uncertain* boxes still surface —
            # the active-learning threshold does the actual routing.
            self._detector = LocalDetector(record.path, conf=self._min_conf)
        return self._detector.detect(image)


class OllamaBackend:
    """The Ollama vision analyzer. Classification-only: the frame's label is
    reported as one full-frame region so it flows through the same routing."""

    def __init__(self, analyzer: OllamaAnalyzer) -> None:
        self._analyzer = analyzer

    def info(self) -> BackendInfo:
        enabled = self._analyzer.enabled
        return BackendInfo(
            id="ollama",
            name=f"Ollama ({self._analyzer.config.model})",
            kind="classifier",
            available=enabled,
            detail="ready" if enabled else "enable AI analysis and start Ollama first",
            boxes=False,
        )

    def predict(self, image: np.ndarray) -> list[Detection] | None:
        result = self._analyzer.analyze(image)
        if result is None:
            return None
        if result.label == "nothing":
            return []
        return [
            Detection(
                label=result.label, confidence=result.confidence,
                cx=0.5, cy=0.5, w=1.0, h=1.0,
            )
        ]


# -- registry -------------------------------------------------------------------


def build_labeling_backend(
    backend_id: str,
    store: Store,
    detector: BuiltinDetector,
    analyzer: OllamaAnalyzer,
) -> LabelingBackend | None:
    """Instantiate the backend a config string names, or None if unknown."""
    if backend_id == "builtin":
        return BuiltinBackend(detector)
    if backend_id == "ollama":
        return OllamaBackend(analyzer)
    if backend_id == "florence2":
        from tailcam.activelearning.florence import Florence2Backend

        return Florence2Backend()
    if backend_id == "qwen2.5-vl":
        from tailcam.activelearning.qwen import QwenVLBackend

        return QwenVLBackend()
    if backend_id.startswith("model:"):
        try:
            model_id = int(backend_id.split(":", 1)[1])
        except ValueError:
            return None
        return RegistryModelBackend(store, model_id)
    return None


def list_labeling_backends(
    store: Store, detector: BuiltinDetector, analyzer: OllamaAnalyzer
) -> list[BackendInfo]:
    """Everything the labeling-model selector can offer, with availability."""
    from tailcam.activelearning.florence import Florence2Backend
    from tailcam.activelearning.qwen import QwenVLBackend

    infos = [BuiltinBackend(detector).info()]
    for record in store.list_models():
        if record.task == "detection" and record.id is not None:
            infos.append(RegistryModelBackend(store, record.id).info())
    infos.append(OllamaBackend(analyzer).info())
    infos.append(Florence2Backend().info())
    infos.append(QwenVLBackend().info())
    return infos


def list_finetune_backends(store: Store) -> list[FinetuneInfo]:
    """Fine-tune targets with per-machine availability (GPU, packages, OS)."""
    from tailcam.activelearning.florence import florence_finetune_support
    from tailcam.activelearning.qwen import qwen_finetune_support
    from tailcam.training.engine import engine_available, torch_device

    yolo_ok = engine_available()
    device = torch_device()
    infos = [
        FinetuneInfo(
            id="yolo",
            name="TailCam YOLO (Ultralytics)",
            available=yolo_ok,
            detail=(
                f"ready · device: {device}" if yolo_ok
                else "install the training engine: pip install 'tailcam[training]'"
            ),
        )
    ]
    fl_ok, fl_detail = florence_finetune_support()
    infos.append(FinetuneInfo(id="florence2", name="Florence-2", available=fl_ok,
                              detail=fl_detail))
    qw_ok, qw_detail = qwen_finetune_support()
    infos.append(FinetuneInfo(id="qwen2.5-vl", name="Qwen2.5-VL (Unsloth)",
                              available=qw_ok, detail=qw_detail))
    return infos


def platform_summary() -> dict:
    """OS + accelerator facts for the UI's capability notes."""
    from tailcam.training.engine import torch_device

    device = torch_device()
    return {
        "os": {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux"),
        "device": device,
        "cuda": device == "cuda",
        "mps": device == "mps",
    }


def model_classes(store: Store, model_id: int) -> list[str]:
    record = store.get_model(model_id)
    if record is None:
        return []
    try:
        return list(json.loads(record.classes_json) or [])
    except (ValueError, TypeError):
        return []
