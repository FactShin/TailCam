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
import stat
import threading
import unicodedata
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

from tailcam.ai.analyzer import Analysis, Detection, OllamaAnalyzer
from tailcam.ai.detector import BuiltinDetector
from tailcam.ai.remote import RemoteDetector
from tailcam.config import TrainingConfig
from tailcam.logging_setup import get_logger
from tailcam.node import RoleDisabledError
from tailcam.persistence.store import Store
from tailcam.storage.models import StorageError

__all__ = ["Detection", "InferenceRouter", "LocalClassifier", "LocalDetector"]

log = get_logger(__name__)


class LocalClassifier:
    """Lazy-loaded Ultralytics classification model."""

    def __init__(self, model_path: str, classes: list[str], *, device: str | None = None) -> None:
        self.model_path = model_path
        self.classes = classes
        self.device = device
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
            result = model.predict(
                image, verbose=False, **({"device": self.device} if self.device else {})
            )[0]
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

    def __init__(self, model_path: str, conf: float = 0.35, *, device: str | None = None) -> None:
        self.model_path = model_path
        self.conf = conf
        self.device = device
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
            result = model.predict(
                image,
                verbose=False,
                conf=self.conf,
                **({"device": self.device} if self.device else {}),
            )[0]
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


class ManagedModelLease:
    """One explicitly reserved model materialization; caller owns its lifetime."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.lease: Any = None

    def resolve(self, record: Any) -> str:
        if self.service is None:
            return record.path
        artifact = self.service.catalog.resolve_alias("model", str(record.id), "file")
        if artifact is None:
            return record.path
        policy = self.service.get_policy()
        self.lease = self.service.workspace_for_artifact(
            artifact.artifact_id,
            max_bytes=policy.workspace_max_bytes // 4,
        )
        try:
            source = self.service.materialize(artifact.artifact_id, self.lease)
            if artifact.metadata.get("format") != "directory-zip":
                return str(source)
            return str(self._extract(source))
        except Exception:
            self.close()
            raise

    def _extract(self, source: Path) -> Path:
        """Validate the complete manifest before creating any extracted file."""
        out = self.lease.path / "model"
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 10000:
                raise StorageError(
                    "unsafe_model_archive", "Model archive has an invalid entry count"
                )
            names: set[str] = set()
            total = 0
            for entry in entries:
                name = entry.filename
                key = unicodedata.normalize("NFC", name).casefold()
                parts = name.split("/")
                reserved = {
                    "con",
                    "prn",
                    "aux",
                    "nul",
                    *(f"com{i}" for i in range(1, 10)),
                    *(f"lpt{i}" for i in range(1, 10)),
                }
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (
                    len(name.encode()) > 1024
                    or name.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or "\\" in name
                    or ":" in name
                    or any(ord(char) < 32 or char in '<>"|?*' for char in name)
                    or any(
                        part.endswith((".", " ")) or part.split(".")[0].casefold() in reserved
                        for part in parts
                    )
                    or mode not in {0, stat.S_IFREG}
                    or entry.is_dir()
                    or entry.flag_bits & 1
                    or key in names
                ):
                    raise StorageError(
                        "unsafe_model_archive", "Model archive contains an unsafe member"
                    )
                names.add(key)
                total += entry.file_size
            if any(
                str(parent).casefold() in names
                for name in names
                for parent in PurePosixPath(name).parents
                if str(parent) != "."
            ):
                raise StorageError(
                    "unsafe_model_archive", "Model archive contains conflicting paths"
                )
            if self.lease.check() + total > self.lease.max_bytes:
                raise StorageError(
                    "workspace_full", "Model does not fit the runtime workspace budget"
                )
            out.mkdir()
            for entry in entries:
                target = out.joinpath(*entry.filename.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(entry) as inp, target.open("xb") as stream:
                    while chunk := inp.read(1024 * 1024):
                        written += len(chunk)
                        if (
                            written > entry.file_size
                            or self.lease.check() + len(chunk) > self.lease.max_bytes
                        ):
                            raise StorageError(
                                "workspace_full", "Model exceeds its declared runtime size"
                            )
                        stream.write(chunk)
                if written != entry.file_size:
                    raise StorageError("unsafe_model_archive", "Model member size did not match")
        self.lease.check()
        return out

    def close(self) -> None:
        if self.lease is not None:
            self.lease.release()
            self.lease = None


class LocalVisionDetector:
    """Use a managed VLM directory through its existing backend adapter."""

    def __init__(
        self, path: str, backend: str, *, managed: bool = False, device: str | None = None
    ) -> None:
        if backend == "florence2":
            from tailcam.activelearning.florence import Florence2Backend

            self.backend: Any = Florence2Backend(
                model_path=path,
                cache_dir=str(Path(path).parent / "cache"),
                local_files_only=managed,
                device=device,
            )
        else:
            from tailcam.activelearning.qwen import QwenVLBackend

            self.backend = QwenVLBackend(
                model_path=path,
                cache_dir=str(Path(path).parent / "cache"),
                local_files_only=managed,
                device=device,
            )

    def load(self) -> bool:
        return bool(self.backend._load())

    def detect(self, image: np.ndarray) -> list[Detection] | None:
        return self.backend.predict(image)


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
        remote: Callable[[], RemoteDetector | None] | None = None,
        role_enabled: Callable[[], bool] | None = None,
        storage_service=None,
        workload_service=None,
    ) -> None:
        self._store = store
        self._storage_service = storage_service
        self._workload_service = workload_service
        self._workload_error = ""
        self._model_lease: ManagedModelLease | None = None
        self._config = config
        self._ollama = ollama
        self._builtin = builtin
        self._role_enabled = role_enabled or (lambda: True)
        # Returns the peer detector when [detection] node routes work elsewhere.
        self._remote = remote or (lambda: None)
        self._lock = threading.RLock()
        self._cached_id: int | None = None
        self._classifier: LocalClassifier | None = None
        self._detector: LocalDetector | LocalVisionDetector | None = None
        self._active_name: str = ""
        self._load_error: str = ""

    def _refresh_active(self) -> None:
        """Load (and cache) the active model as a classifier or detector, by task.
        Caller holds ``self._lock``. Records why a model ISN'T running in
        ``_load_error`` so the UI can say so instead of silently falling back."""
        if self._workload_service is not None:
            return  # model loading belongs to the isolated worker
        # Even status endpoints call this method. Disabled analysis must not
        # load an old selected model just because someone opens the dashboard.
        if not self._role_enabled():
            self.shutdown()
            self._load_error = "analysis role disabled"
            return
        mid = self._config.active_model_id
        if self._cached_id == mid:
            return
        if not mid:
            self.shutdown()
            self._cached_id = mid
            return
        record = self._store.get_model(mid)
        if record is None:
            self._load_error = f"active model #{mid} no longer exists"
            return
        candidate_lease = ManagedModelLease(self._storage_service)
        try:
            model_path = candidate_lease.resolve(record)
            if not model_path:
                self._load_error = "this model has no weights yet — train it first"
                candidate_lease.close()
                return
            detector = None
            classifier = None
            if record.task == "detection":
                detector = (
                    LocalVisionDetector(
                        model_path, record.base_model, managed=candidate_lease.lease is not None
                    )
                    if record.base_model in {"florence2", "qwen2.5-vl"}
                    else LocalDetector(model_path, self._config.detect_conf)
                )
                loaded = detector.load()
            else:
                try:
                    classes = json.loads(record.classes_json) or []
                except (ValueError, TypeError):
                    classes = []
                classifier = LocalClassifier(model_path, classes)
                loaded = classifier.load()
            if candidate_lease.lease is not None:
                candidate_lease.lease.check()
            if not loaded:
                self._load_error = "model failed to load (is the training engine installed?)"
                candidate_lease.close()
                return
        except (StorageError, OSError, ValueError, zipfile.BadZipFile) as exc:
            candidate_lease.close()
            self._load_error = str(exc)
            return  # preserve the previous loaded model and its lease
        previous_lease = self._model_lease
        self._cached_id = mid
        self._classifier, self._detector = classifier, detector
        self._active_name, self._load_error = record.name, ""
        self._model_lease = candidate_lease
        if previous_lease is not None:
            previous_lease.close()

    def shutdown(self) -> None:
        with self._lock:
            self._classifier = None
            self._detector = None
            self._cached_id = None
            self._active_name = ""
            if self._model_lease is not None:
                self._model_lease.close()
                self._model_lease = None

    def _active_classifier(self) -> LocalClassifier | None:
        with self._lock:
            self._refresh_active()
            return self._classifier

    def _active_detector(self) -> LocalDetector | LocalVisionDetector | None:
        with self._lock:
            self._refresh_active()
            return self._detector

    def _source_role_enabled(self) -> bool:
        if self._role_enabled():
            return True
        jobs = getattr(self._workload_service, "jobs", None)
        return "capture" in getattr(jobs, "active_roles", ())

    def _legacy_remote(self, task: str):
        if not self._source_role_enabled() or self._workload_service is None:
            return None
        if task in self._workload_service.jobs.get_policy().routes:
            return None
        if task == "motion_description" and self._workload_service.config.ai.enabled:
            return None
        return self._remote()

    @property
    def enabled(self) -> bool:
        if self._workload_service is not None:
            if not self._source_role_enabled():
                return False
            cfg = self._workload_service.config
            return bool(cfg.ai.enabled or cfg.detection.enabled or cfg.training.active_model_id)
        with self._lock:
            self._refresh_active()
            local = self._classifier is not None or self._detector is not None
        return (
            local
            or (self._role_enabled() and self._ollama.enabled)
            or (self._remote() is not None)
            or (self._role_enabled() and self._builtin is not None and self._builtin.enabled)
        )

    @property
    def detection_active(self) -> bool:
        """True when something produces bounding boxes (for the UI overlay) —
        a trained detection model, a detection node, or the built-in detector."""
        if self._workload_service is not None:
            return self._source_role_enabled() and bool(
                self._workload_service.config.detection.enabled
            )
        if self._active_detector() is not None:
            return True
        if self._remote() is not None:
            return True
        return self._role_enabled() and self._builtin is not None and self._builtin.enabled

    def detection_note(self) -> str:
        """Status line for the overlay badge while the built-in detector is
        provisioning itself ("downloading model 42%") or failing."""
        if self._workload_service is not None:
            return self._workload_error
        if self._active_detector() is not None:
            return ""
        remote = self._remote()
        if remote is not None:
            if not remote.available:
                return f"detection node unreachable: {remote.last_error or remote.label}"
            return ""
        if not self._role_enabled() or self._builtin is None:
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
        if self._workload_service is not None:
            if not self.enabled:
                return {"mode": "off", "model_name": "", "task": "", "error": ""}
            remote = self._legacy_remote("live_detection")
            if remote is not None:
                return {
                    "mode": "remote",
                    "model_name": remote.model_name(),
                    "task": "detection",
                    "error": "" if remote.available else remote.last_error,
                }
            config = self._workload_service.config
            active = (
                self._store.get_model(config.training.active_model_id)
                if (config.training.active_model_id)
                else None
            )
            error = self._workload_error
            if config.training.active_model_id and active is None:
                error = "Selected model no longer exists"
            mode = "local" if active else "ollama" if config.ai.enabled else "builtin"
            name = (
                active.name
                if active
                else config.ai.model
                if config.ai.enabled
                else (config.detection.model or "Built-in detector")
            )
            if config.detection.node or self._workload_service.jobs.get_policy().routes:
                mode, name = "remote", "Placed worker"
            return {
                "mode": mode,
                "model_name": name,
                "task": active.task if active else "detection",
                "error": error,
            }
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
        if self._role_enabled() and self._ollama.enabled:
            return {
                "mode": "ollama",
                "model_name": self._ollama.config.model,
                "task": "classification",
                "error": err,  # e.g. selected local model failed -> tell the user
            }
        remote = self._remote()
        if remote is not None:
            return {
                "mode": "remote",
                "model_name": remote.model_name(),
                "task": "detection",
                "error": (err if self._role_enabled() else "")
                or ("" if remote.available else remote.last_error),
            }
        if self._role_enabled() and self._builtin is not None and self._builtin.enabled:
            s = self._builtin.status()
            return {
                "mode": "builtin",
                "model_name": s.model,
                "task": "detection",
                "error": err or s.error,
            }
        return {"mode": "off", "model_name": "", "task": "", "error": err}

    def analyze(self, image: np.ndarray) -> Analysis | None:
        if self._workload_service is not None:
            if not self._source_role_enabled():
                return None
            remote = self._legacy_remote("motion_description")
            if remote is not None:
                return remote.analyze(image)
            return self._routed_analysis(image)
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
        if self._role_enabled() and self._ollama.enabled:
            return self._ollama.analyze(image)
        # A detection node labels for us (its own full pipeline runs there).
        remote = self._remote()
        if remote is not None:
            result = remote.analyze(image)
            if result is not None:
                return result
        # Zero-config fallback: label motion events with the built-in detector's
        # best box, so event badges (PERSON, CUP, DOG …) work with no setup.
        if self._role_enabled() and self._builtin is not None and self._builtin.enabled:
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

    def detect(self, image: np.ndarray, *, camera_id: str = "") -> list[Detection] | None:
        """Bounding boxes from the active detection model, else the built-in
        detector. Returns None only when no box source exists at all (the live
        overlay treats that as 'detection unavailable')."""
        if self._workload_service is not None:
            if not self._source_role_enabled():
                return None
            remote = self._legacy_remote("live_detection")
            if remote is not None:
                return remote.detect(image)
            return self._routed_detection(image, camera_id=camera_id)
        with self._lock:
            self._refresh_active()
            det = self._detector
            if det is not None:
                return det.detect(image)
        remote = self._remote()
        if remote is not None:
            boxes = remote.detect(image)
            return boxes  # None means unavailable; [] means a successful empty result.
        if self._role_enabled() and self._builtin is not None and self._builtin.enabled:
            return self._builtin.detect(image)
        return None

    def _routed_detection(self, image, *, local_only: bool = False, camera_id: str = ""):
        from tailcam.jobs.models import JobError
        from tailcam.workloads.process import ExecutionError

        try:
            result = self._workload_service.detect(
                image, local_only=local_only, camera_id=camera_id
            )
            if result.get("available") is not True:
                return None
            self._workload_error = ""
            return [
                Detection(**box)
                for prediction in result.get("predictions", [])
                for box in prediction.get("boxes", [])
            ]
        except (JobError, ExecutionError) as exc:
            self._workload_error = exc.detail
            return None
        except RoleDisabledError as exc:
            self._workload_error = str(exc)
            return None

    def workload_status(self, camera_id: str = "") -> dict:
        if self._workload_service is None:
            return {}
        if not camera_id.startswith("local:"):
            remote = self._legacy_remote("live_detection")
            if remote is not None:
                status: dict[str, Any] = getattr(remote, "status", lambda: {})()
                return {
                    key: status[key] for key in ("model_name", "round_trip_ms") if key in status
                }
        return self._workload_service.status(camera_id)

    def _routed_analysis(self, image, *, local_only: bool = False):
        from tailcam.jobs.models import JobError
        from tailcam.workloads.process import ExecutionError

        try:
            result = self._workload_service.analyze(image, local_only=local_only)
            predictions = result.get("predictions", [])
            if not predictions:
                return None
            prediction = predictions[0]
            if "boxes" in prediction:
                boxes = prediction["boxes"]
                if not boxes:
                    return Analysis("nothing", "No objects detected", 1.0)
                best = max(boxes, key=lambda box: box["confidence"])
                return Analysis(best["label"], "Detected " + best["label"], best["confidence"])
            if "label" not in prediction:
                return None
            self._workload_error = ""
            return Analysis(
                prediction["label"], prediction["description"], prediction["confidence"]
            )
        except (JobError, ExecutionError) as exc:
            self._workload_error = exc.detail
            return None
        except RoleDisabledError as exc:
            self._workload_error = str(exc)
            return None

    def detect_local(self, image):
        if not self._role_enabled():
            return None
        if self._workload_service is not None:
            return self._routed_detection(image, local_only=True)
        with self._lock:
            detector = self._active_detector()
            if detector is not None:
                return detector.detect(image)
            if self._role_enabled() and self._builtin is not None and self._builtin.ready:
                return self._builtin.detect(image)
        return None

    def analyze_local(self, image):
        if not self._role_enabled():
            return None
        if self._workload_service is not None:
            return self._routed_analysis(image, local_only=True)
        with self._lock:
            classifier = self._active_classifier()
            if classifier is not None:
                return classifier.analyze(image)
        if self._role_enabled() and self._ollama.enabled:
            return self._ollama.analyze(image)
        return None
