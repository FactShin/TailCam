"""The active learning loop: capture → infer → route → review → sync → train.

Mirrors :class:`tailcam.training.service.TrainingService`'s shape — a daemon
thread sampling frames on an interval, SQLite for state, config for settings —
so the two services feel like one system. The loop never blocks a request
thread; start/stop/sync/train are all safe to call from FastAPI handlers.

Routing rule (the "active" in active learning):

- every detection at/above the confidence threshold → auto-label, stored as a
  machine-labeled sample with its boxes;
- any detection below the threshold (or, optionally, an empty frame) → the
  frame goes to Label Studio for human review, with the model's boxes attached
  as pre-annotations so the reviewer corrects instead of starting from zero.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from tailcam import paths
from tailcam.activelearning.annotations import (
    SOURCE_HUMAN,
    SOURCE_MACHINE,
    AnnotatedFrame,
    FrameAnnotation,
    to_florence_od_string,
    to_qwen_json,
)
from tailcam.activelearning.backends import (
    LabelingBackend,
    build_labeling_backend,
    list_finetune_backends,
)
from tailcam.activelearning.labelstudio import LabelStudioError, LabelStudioService
from tailcam.ai.analyzer import Detection
from tailcam.camera.manager import CameraManager
from tailcam.config import ActiveLearningConfig, AppConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.models import (
    DatasetSampleRecord,
    ModelRecord,
    ReviewItemRecord,
    SampleAnnotationRecord,
    TrainingRunRecord,
)
from tailcam.persistence.store import Store

log = get_logger(__name__)

_THUMB_WIDTH = 240

# Sample.source values written by this pipeline (extends collect/motion/…).
SAMPLE_SOURCE_AUTO = "active-auto"  # machine-labeled, confident
SAMPLE_SOURCE_REVIEW = "active-review"  # awaiting / carrying human labels


def route_frame(
    detections: list[Detection] | None,
    threshold: float,
    review_empty: bool = False,
) -> str:
    """Decide what happens to a frame: ``auto`` (confident machine label),
    ``review`` (human should look), or ``skip`` (nothing to learn from).

    Inference failure (None) is a skip — an unhealthy model must not flood
    Label Studio. An empty result is only reviewed when the user opted in.
    """
    if detections is None:
        return "skip"
    if not detections:
        return "review" if review_empty else "skip"
    if all(d.confidence >= threshold for d in detections):
        return "auto"
    return "review"


@dataclass
class SessionStats:
    """Counters for the current (or last) active learning session."""

    running: bool = False
    started_ts: float | None = None
    frames_processed: int = 0
    auto_labeled: int = 0
    sent_for_review: int = 0
    skipped: int = 0
    errors: int = 0
    last_error: str = ""
    labeling_model: str = ""
    dataset_id: int = 0
    # dataset-source runs: sample ids already inferenced this session
    seen_sample_ids: set[int] = field(default_factory=set)


class ActiveLearningService:
    def __init__(
        self,
        manager: CameraManager,
        store: Store,
        config: AppConfig,
        detector,
        analyzer,
        training,
        host: str,
        label_studio: LabelStudioService | None = None,
    ) -> None:
        self._manager = manager
        self._store = store
        self._app_config = config
        self._config: ActiveLearningConfig = config.active_learning
        self._detector = detector
        self._analyzer = analyzer
        self._training = training  # TrainingService (owns the YOLO fine-tune path)
        self._host = host
        self.label_studio = label_studio or LabelStudioService(self._config)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stats = SessionStats()
        self._backend: LabelingBackend | None = None
        self._run_stops: dict[int, threading.Event] = {}

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        """Validate the configuration and start the monitoring loop.

        Raises ValueError/LabelStudioError with a user-facing message when a
        precondition fails (bad model, unreachable Label Studio, …) — nothing
        is left half-started.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("active learning is already running")
        cfg = self._config
        backend = build_labeling_backend(
            cfg.labeling_model, self._store, self._detector, self._analyzer
        )
        if backend is None:
            raise ValueError(f"unknown labeling model '{cfg.labeling_model}'")
        info = backend.info()
        if not info.available:
            raise ValueError(f"labeling model {info.name} is not available: {info.detail}")
        dataset_id = self._resolve_dataset()
        # Verify Label Studio up front (connection + project), so the loop
        # never discovers a bad token mid-session.
        self.label_studio.ensure_project(self._label_names())
        self._app_config.save()  # persists a newly created project id
        with self._lock:
            self._backend = backend
            self._stats = SessionStats(
                running=True,
                started_ts=time.time(),
                labeling_model=cfg.labeling_model,
                dataset_id=dataset_id,
            )
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="active-learning", daemon=True
            )
            self._thread.start()
        log.info(
            "active learning started: model=%s source=%s threshold=%.2f dataset=%s",
            cfg.labeling_model, cfg.source, cfg.confidence_threshold, dataset_id,
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=6.0)
        self._thread = None
        with self._lock:
            self._stats.running = False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def shutdown(self) -> None:
        self.stop()

    def stats(self) -> SessionStats:
        with self._lock:
            # Deep-copy so callers get a stable snapshot (the sets keep mutating
            # on the worker thread after the lock is released).
            stats = copy.deepcopy(self._stats)
        stats.running = self.is_running()
        return stats

    # -- setup helpers ---------------------------------------------------------
    def _resolve_dataset(self) -> int:
        """The dataset this session writes into. Dataset-source runs label the
        source dataset in place; otherwise use (or auto-create) the configured
        detection dataset."""
        cfg = self._config
        if cfg.source.startswith("dataset:"):
            try:
                ds_id = int(cfg.source.split(":", 1)[1])
            except ValueError as exc:
                raise ValueError(f"bad dataset source '{cfg.source}'") from exc
            if self._store.get_dataset(ds_id) is None:
                raise ValueError(f"source dataset #{ds_id} not found")
            return ds_id
        if cfg.dataset_id and self._store.get_dataset(cfg.dataset_id) is not None:
            return cfg.dataset_id
        record = self._training.create_dataset("Active learning", task="detection")
        cfg.dataset_id = record.id or 0
        return cfg.dataset_id

    def _label_names(self) -> list[str]:
        """Classes offered in the Label Studio project: the training classes
        plus anything already annotated in the target dataset."""
        names = list(self._app_config.training.classes)
        ds_id = self._config.dataset_id
        if ds_id:
            for label in self._store.dataset_annotation_label_counts(ds_id):
                if label not in names:
                    names.append(label)
        return [n for n in names if n and n != "nothing"] or ["object"]

    # -- the loop ---------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._config.source.startswith("dataset:"):
                    idle = self._process_dataset_batch()
                else:
                    self._process_cameras()
                    idle = False
                if idle:
                    self._sleep(max(5.0, self._config.interval_seconds))
                else:
                    self._sleep(max(1.0, self._config.interval_seconds))
            except Exception as exc:  # keep the loop alive, surface the error
                log.warning("active learning loop error: %s", exc)
                with self._lock:
                    self._stats.errors += 1
                    self._stats.last_error = str(exc)[:300]
                self._sleep(max(2.0, self._config.interval_seconds))

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stop.is_set():
            time.sleep(0.25)

    def _camera_ids(self) -> list[str]:
        source = self._config.source
        if source.startswith("camera:"):
            return [source.split(":", 1)[1]]
        return [cam.descriptor.id for cam in self._manager.list()]

    def _process_cameras(self) -> None:
        for camera_id in self._camera_ids():
            if self._stop.is_set():
                return
            buffer = self._manager.get_buffer(camera_id)
            if buffer is None:
                continue
            frame = buffer.await_latest(-1, timeout=1.0)
            if frame is None:
                continue
            self._handle_frame(frame.image, camera_id=camera_id)

    def _process_dataset_batch(self, batch: int = 8) -> bool:
        """Run inference over a source dataset's not-yet-labeled samples.
        Returns True when there was nothing left to do (idle)."""
        stats = self._stats
        ds_id = stats.dataset_id
        # SQL excludes annotated + under-review rows; we additionally skip the
        # ones already inferenced *this session* (seen_sample_ids — in-memory,
        # e.g. frames where the model saw nothing). Fetch a window large enough
        # to still yield `batch` fresh rows after that filter, instead of
        # loading the whole samples table.
        with self._lock:
            seen = set(stats.seen_sample_ids)
        window = self._store.list_unprocessed_samples(ds_id, limit=batch + len(seen))
        todo = [s for s in window if s.id is not None and s.id not in seen]
        for sample in todo[:batch]:
            if self._stop.is_set():
                return False
            with self._lock:
                stats.seen_sample_ids.add(sample.id or 0)
            image = cv2.imread(sample.path)
            if image is None:
                continue
            self._handle_frame(
                image, camera_id=sample.camera_id, existing_sample=sample
            )
        return not todo

    # -- per-frame handling -------------------------------------------------------
    def _handle_frame(
        self,
        image: np.ndarray,
        camera_id: str = "",
        existing_sample: DatasetSampleRecord | None = None,
    ) -> None:
        backend = self._backend
        if backend is None:  # pragma: no cover - loop only runs after start()
            return
        cfg = self._config
        detections = backend.predict(image)
        decision = route_frame(detections, cfg.confidence_threshold, cfg.review_empty_frames)
        with self._lock:
            self._stats.frames_processed += 1
            if decision == "skip":
                self._stats.skipped += 1
        if decision == "skip":
            return
        if decision == "review" and self._review_budget_spent():
            with self._lock:
                self._stats.skipped += 1
                self._stats.last_error = (
                    "review cap reached for this session (max_review_per_session)"
                )
            return
        annotations = [
            FrameAnnotation(
                label=d.label, cx=d.cx, cy=d.cy, w=d.w, h=d.h,
                confidence=d.confidence, source=SOURCE_MACHINE,
            )
            for d in (detections or [])
        ]
        sample = existing_sample or self._save_sample(
            image, camera_id, decision, annotations
        )
        if sample is None or sample.id is None:
            return
        if decision == "auto":
            self._apply_machine_labels(sample.id, annotations)
            with self._lock:
                self._stats.auto_labeled += 1
            return
        # decision == "review"
        try:
            frame = AnnotatedFrame(
                image_path=sample.path,
                annotations=annotations,
                camera_id=camera_id,
                timestamp=sample.created_ts,
                labeling_model=cfg.labeling_model,
            )
            task_id = self.label_studio.submit_frame(
                cfg.project_id, frame, cfg.labeling_model
            )
        except LabelStudioError as exc:
            with self._lock:
                self._stats.errors += 1
                self._stats.last_error = str(exc)
            return
        lowest = min((a.confidence for a in annotations if a.confidence is not None), default=None)
        self._store.add_review_item(
            ReviewItemRecord(
                id=None,
                sample_id=sample.id,
                dataset_id=self._stats.dataset_id,
                ls_project_id=cfg.project_id,
                ls_task_id=task_id,
                status="pending",
                labeling_model=cfg.labeling_model,
                confidence=lowest,
                created_ts=time.time(),
            )
        )
        with self._lock:
            self._stats.sent_for_review += 1

    def _review_budget_spent(self) -> bool:
        cap = self._config.max_review_per_session
        if cap <= 0:
            return False
        with self._lock:
            return self._stats.sent_for_review >= cap

    def _apply_machine_labels(
        self, sample_id: int, annotations: list[FrameAnnotation]
    ) -> None:
        ts = time.time()
        self._store.replace_annotations(
            sample_id,
            [
                SampleAnnotationRecord(
                    id=None, sample_id=sample_id, label=a.label,
                    cx=a.cx, cy=a.cy, w=a.w, h=a.h, created_ts=ts,
                )
                for a in annotations
            ],
        )
        top = max(annotations, key=lambda a: a.confidence or 0.0)
        self._store.set_sample_machine_label(sample_id, top.label, top.confidence)

    def _save_sample(
        self,
        image: np.ndarray,
        camera_id: str,
        decision: str,
        annotations: list[FrameAnnotation],
    ) -> DatasetSampleRecord | None:
        from tailcam.streaming.encoder import encode_jpeg

        dataset_id = self._stats.dataset_id
        ts = time.time()
        stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        safe = (camera_id or "frame").replace("/", "_")
        frames_dir = paths.datasets_dir() / str(dataset_id) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        path = frames_dir / f"al_{safe}_{stamp}.jpg"
        try:
            path.write_bytes(encode_jpeg(image, 88))
        except OSError as exc:
            log.warning("active learning: could not write frame: %s", exc)
            return None
        thumb = self._write_thumb(image, dataset_id, path.stem)
        source = SAMPLE_SOURCE_AUTO if decision == "auto" else SAMPLE_SOURCE_REVIEW
        confidences = [a.confidence for a in annotations if a.confidence is not None]
        record = DatasetSampleRecord(
            id=None,
            dataset_id=dataset_id,
            path=str(path),
            thumb=str(thumb) if thumb else None,
            label=None,
            source=source,
            camera_id=camera_id,
            host=self._host,
            created_ts=ts,
            confidence=min(confidences) if confidences else None,
        )
        record.id = self._store.add_sample(record)
        return record

    def _write_thumb(self, image: np.ndarray, dataset_id: int, stem: str) -> Path | None:
        from tailcam.streaming.encoder import encode_jpeg

        try:
            h, w = image.shape[:2]
            scale = _THUMB_WIDTH / max(1, w)
            thumb = cv2.resize(image, (_THUMB_WIDTH, max(1, int(h * scale))))
            thumbs = paths.datasets_dir() / str(dataset_id) / "thumbs"
            thumbs.mkdir(parents=True, exist_ok=True)
            out = thumbs / f"{stem}.jpg"
            out.write_bytes(encode_jpeg(thumb, quality=72))
            return out
        except Exception:  # pragma: no cover
            return None

    # -- syncing completed annotations ------------------------------------------
    def sync(self) -> dict:
        """Pull completed annotations from Label Studio and write them onto
        their samples. Returns {'completed', 'pending', 'dataset_version'}."""
        cfg = self._config
        pending = self._store.list_review_items(status="pending")
        if not pending:
            counts = self._store.review_counts()
            return {
                "completed": 0,
                "pending": counts.get("pending", 0),
                "dataset_version": 0,
            }
        by_path = {self._store.get_sample(i.sample_id).path: i  # type: ignore[union-attr]
                   for i in pending if self._store.get_sample(i.sample_id) is not None}
        project_ids = {i.ls_project_id for i in pending if i.ls_project_id} or (
            {cfg.project_id} if cfg.project_id else set()
        )
        completed_count = 0
        touched_datasets: set[int] = set()
        ts = time.time()
        for project_id in project_ids:
            for task in self.label_studio.pull_completed(project_id):
                item = by_path.get(task.image_path)
                if item is None or item.id is None:
                    continue
                self._store.replace_annotations(
                    item.sample_id,
                    [
                        SampleAnnotationRecord(
                            id=None, sample_id=item.sample_id, label=a.label,
                            cx=a.cx, cy=a.cy, w=a.w, h=a.h, created_ts=ts,
                        )
                        for a in task.annotations
                    ],
                )
                top = max(task.annotations, key=lambda a: a.w * a.h)
                self._store.set_sample_label(item.sample_id, top.label)
                self._store.update_review_item(
                    item.id, status="completed", completed_ts=ts,
                    ls_task_id=task.task_id or item.ls_task_id,
                )
                touched_datasets.add(item.dataset_id)
                completed_count += 1
        version = 0
        for ds_id in touched_datasets:
            version = self._store.bump_dataset_version(ds_id)
        counts = self._store.review_counts()
        if completed_count:
            log.info("active learning: synced %d human annotation(s)", completed_count)
        return {
            "completed": completed_count,
            "pending": counts.get("pending", 0),
            "dataset_version": version,
        }

    # -- fine-tuning ----------------------------------------------------------------
    def train(self, epochs: int | None = None) -> TrainingRunRecord | None:
        """Fine-tune the configured target model on the accumulated dataset.

        ``yolo`` delegates to the existing Ultralytics pipeline (same runs UI,
        same registry). Florence-2 / Qwen2.5-VL run their own trainer threads
        but report through the same TrainingRunRecord, so progress and errors
        land in the familiar place.
        """
        cfg = self._config
        dataset_id = cfg.dataset_id
        if cfg.source.startswith("dataset:"):
            dataset_id = int(cfg.source.split(":", 1)[1])
        if not dataset_id or self._store.get_dataset(dataset_id) is None:
            raise ValueError("no active-learning dataset yet — run a session first")
        target = cfg.finetune_model
        if target == "yolo":
            return self._training.train(dataset_id, epochs=epochs)
        if target not in ("florence2", "qwen2.5-vl"):
            raise ValueError(f"unknown fine-tune target '{target}'")
        support = {b.id: b for b in list_finetune_backends(self._store)}[target]
        if not support.available:
            raise RuntimeError(f"{support.name} fine-tuning unavailable: {support.detail}")
        if self._training.has_active_run():
            raise RuntimeError(
                "a training run is already in progress — stop it or wait for it to finish"
            )
        ep = epochs or (3 if target == "florence2" else 1)
        run = TrainingRunRecord(
            id=None,
            dataset_id=dataset_id,
            model_id=None,
            base_model=target,
            status="queued",
            params_json=json.dumps({"epochs": ep, "task": "detection", "target": target}),
            metrics_json="{}",
            log="",
            epochs=ep,
            epoch=0,
            created_ts=time.time(),
        )
        run.id = self._store.add_run(run)
        stop = threading.Event()
        with self._lock:
            self._run_stops[run.id] = stop
        threading.Thread(
            target=self._vlm_train_job,
            args=(run.id, dataset_id, target, ep, stop),
            name=f"al-train-{run.id}",
            daemon=True,
        ).start()
        return self._store.get_run(run.id)

    def stop_run(self, run_id: int) -> bool:
        with self._lock:
            stop = self._run_stops.get(run_id)
        if stop is None:
            return False
        stop.set()
        return True

    def _export_pairs(self, dataset_id: int, target: str) -> list[tuple[str, str]]:
        """(image_path, model-format target string) pairs for a VLM fine-tune."""
        pairs: list[tuple[str, str]] = []
        for sample in self._store.list_samples(dataset_id, limit=1_000_000):
            if sample.id is None or not Path(sample.path).exists():
                continue
            boxes = self._store.list_annotations(sample.id)
            if not boxes:
                continue
            anns = [
                FrameAnnotation(label=b.label, cx=b.cx, cy=b.cy, w=b.w, h=b.h,
                                source=SOURCE_HUMAN)
                for b in boxes
            ]
            if target == "florence2":
                pairs.append((sample.path, to_florence_od_string(anns)))
            else:
                image = cv2.imread(sample.path)
                if image is None:
                    continue
                h, w = image.shape[:2]
                pairs.append((sample.path, to_qwen_json(anns, w, h)))
        return pairs

    def _vlm_train_job(
        self, run_id: int, dataset_id: int, target: str, epochs: int, stop: threading.Event
    ) -> None:
        try:
            self._store.update_run(run_id, status="preparing", started_ts=time.time())
            pairs = self._export_pairs(dataset_id, target)
            if not pairs:
                raise ValueError("no annotated samples — label some frames first")
            self._store.update_run(
                run_id, status="training", log=f"{len(pairs)} annotated sample(s)"
            )
            out_dir = paths.models_dir() / f"run-{run_id}" / target
            on_epoch = lambda e: self._store.update_run(run_id, epoch=e)  # noqa: E731
            if target == "florence2":
                from tailcam.activelearning.florence import finetune_florence

                result = finetune_florence(
                    pairs, out_dir, epochs=epochs, on_epoch=on_epoch,
                    should_stop=stop.is_set,
                )
            else:
                from tailcam.activelearning.qwen import finetune_qwen

                result = finetune_qwen(
                    pairs, out_dir, epochs=epochs, on_epoch=on_epoch,
                    should_stop=stop.is_set,
                )
            if stop.is_set():
                self._store.update_run(run_id, status="stopped", ended_ts=time.time())
                return
            classes = sorted(
                self._store.dataset_annotation_label_counts(dataset_id)
            )
            name = {"florence2": "Florence-2", "qwen2.5-vl": "Qwen2.5-VL"}[target]
            model_id = self._store.add_model(
                ModelRecord(
                    id=None,
                    name=f"{name} fine-tune {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    kind="trained",
                    path=result["model_path"],
                    classes_json=json.dumps(classes),
                    base_model=target,
                    metrics_json=json.dumps(result.get("metrics", {})),
                    created_ts=time.time(),
                    task="detection",
                )
            )
            self._store.update_run(
                run_id, status="complete", model_id=model_id, epoch=epochs,
                metrics_json=json.dumps(result.get("metrics", {})), ended_ts=time.time(),
            )
            log.info("active learning: %s fine-tune run %s complete -> model %s",
                     target, run_id, model_id)
        except Exception as exc:
            log.exception("active learning: fine-tune run %s failed: %s", run_id, exc)
            self._store.update_run(
                run_id, status="error", log=str(exc)[:500], ended_ts=time.time()
            )
        finally:
            with self._lock:
                self._run_stops.pop(run_id, None)
