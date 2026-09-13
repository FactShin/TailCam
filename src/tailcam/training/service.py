"""Training data + model management.

- Collection: a background thread samples a frame from every online camera on an
  interval and adds it (optionally weak-labeled by the Ollama model) to the
  active dataset — so "all my camera feeds train the model".
- Datasets/samples: create, list, relabel, delete, and import existing motion
  events as labeled samples.
- Models: a registry of the base ("our") model, models you've trained, and
  bring-your-own ``.pt`` files, with one marked active for inference.

Actual training execution lives in :mod:`tailcam.training.runner` (added with
the inference phase); this module owns the data + registry.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from tailcam import paths
from tailcam.camera.manager import CameraManager
from tailcam.config import TrainingConfig
from tailcam.logging_setup import get_logger
from tailcam.media.storage import (
    ProducerWorkspace,
    alias,
    archive_tree,
    bounded_copy,
    enabled,
    local_path,
    safe_filename,
    sample_path,
    store_image,
)
from tailcam.persistence.models import (
    DatasetRecord,
    DatasetSampleRecord,
    ModelRecord,
    SampleAnnotationRecord,
    TrainingRunRecord,
)
from tailcam.persistence.store import Store
from tailcam.storage.models import StorageError
from tailcam.streaming.encoder import encode_jpeg

log = get_logger(__name__)

_THUMB_WIDTH = 240
BASE_MODEL_NAME = "TailCam base (YOLO11n-cls)"


class TrainingService:
    def __init__(
        self,
        manager: CameraManager,
        store: Store,
        config: TrainingConfig,
        analyzer,
        host: str,
        notifier=None,
        *,
        role_check: Callable[[], None] | None = None,
        analysis_check: Callable[[], None] | None = None,
        storage_service=None,
        job_service=None,
    ) -> None:
        self._manager = manager
        self._store = store
        self._config = config
        self._analyzer = analyzer
        self._host = host
        self._notifier = notifier
        self._role_check = role_check or (lambda: None)
        self._storage_service = storage_service
        self._job_service = job_service
        self._job_lock = threading.RLock()
        self._analysis_check = analysis_check or self._role_check
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._storage_jobs: dict[int, ProducerWorkspace] = {}
        self.collected_this_session = 0
        self._run_stops: dict[int, threading.Event] = {}

    # -- startup -----------------------------------------------------------
    def startup(self) -> None:
        self._role_check()
        self._seed_base_model()
        if self._config.collect_enabled:
            self.start_collection()

    def _seed_base_model(self) -> None:
        """Ensure the 'use our model' base entry exists in the registry."""
        if any(m.kind == "base" for m in self._store.list_models()):
            return
        self._store.add_model(
            ModelRecord(
                id=None,
                name=BASE_MODEL_NAME,
                kind="base",
                path="",  # downloaded by Ultralytics on first train
                classes_json="[]",
                base_model=self._config.base_model,
                metrics_json="{}",
                created_ts=time.time(),
            )
        )

    # -- collection --------------------------------------------------------
    def start_collection(self) -> None:
        self._role_check()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="training-collect", daemon=True)
            self._thread.start()
        log.info("training: dataset collection started")

    def stop_collection(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=6.0)
        self._thread = None

    def is_collecting(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def shutdown(self) -> None:
        self.stop_collection()

    def _run(self) -> None:
        while not self._stop.is_set():
            ds_id = self._config.active_dataset_id
            if ds_id and self._store.get_dataset(ds_id) is not None:
                for cam in self._manager.list():
                    if self._stop.is_set():
                        break
                    try:
                        self._capture_sample(ds_id, cam.descriptor.id)
                    except Exception as exc:  # pragma: no cover - keep the loop alive
                        log.warning("training: sample capture failed: %s", exc)
            self._sleep(max(2.0, self._config.collect_interval_seconds))

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stop.is_set():
            time.sleep(0.25)

    def _capture_sample(self, dataset_id: int, camera_id: str) -> None:
        buffer = self._manager.get_buffer(camera_id)
        if buffer is None:
            return
        frame = buffer.await_latest(-1, timeout=1.0)
        if frame is None:
            return
        label: str | None = None
        confidence: float | None = None
        if self._config.auto_label and self._analyzer.enabled:
            result = self._analyzer.analyze(frame.image)
            if result is not None:
                label, confidence = result.label, result.confidence
        self._save_sample(
            dataset_id, camera_id, frame.image, "collect", label=label, confidence=confidence
        )

    def _save_sample(
        self,
        dataset_id: int,
        camera_id: str,
        image: np.ndarray,
        source: str,
        label: str | None = None,
        confidence: float | None = None,
    ) -> int:
        self._role_check()
        if enabled(self._storage_service):
            service = self._storage_service
            policy_snapshot = service.get_policy().model_copy(deep=True)
            annotation_plan = (
                service.admit(
                    "annotation",
                    camera_id=camera_id,
                    expected_bytes=len(json.dumps({"label": label}).encode()) + 512,
                    policy_snapshot=policy_snapshot,
                )
                if label is not None
                else None
            )
            artifact, thumb = store_image(
                service,
                "training_sample",
                image,
                camera_id=camera_id,
                policy_snapshot=policy_snapshot,
                metadata={"dataset_id": dataset_id, "source": source, "host": self._host},
            )
            sample_id = self._store.add_sample(
                DatasetSampleRecord(
                    id=None,
                    dataset_id=dataset_id,
                    path=local_path(service, artifact),
                    thumb=local_path(service, thumb) or None,
                    label=None,
                    source=source,
                    camera_id=camera_id,
                    host=self._host,
                    created_ts=time.time(),
                    confidence=confidence,
                )
            )
            alias(service, "sample", sample_id, "file", artifact)
            alias(service, "sample", sample_id, "thumbnail", thumb)
            if annotation_plan is not None:
                annotation = self._publish_annotation(
                    sample_id, label, [], admission=annotation_plan
                )
                self._store.set_sample_label(sample_id, label)
                alias(service, "sample", sample_id, "annotation", annotation)
            self.collected_this_session += 1
            return sample_id
        ts = time.time()
        stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        safe = safe_filename(camera_id)
        frames_dir = paths.datasets_dir() / str(dataset_id) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        path = frames_dir / f"{safe}_{stamp}.jpg"
        path.write_bytes(encode_jpeg(image, 88))
        thumb = _write_thumb(image, dataset_id, path.stem)
        sample_id = self._store.add_sample(
            DatasetSampleRecord(
                id=None,
                dataset_id=dataset_id,
                path=str(path),
                thumb=str(thumb) if thumb else None,
                label=label,
                source=source,
                camera_id=camera_id,
                host=self._host,
                created_ts=ts,
                confidence=confidence,
            )
        )
        self.collected_this_session += 1
        return sample_id

    # -- datasets ----------------------------------------------------------
    def create_dataset(
        self, name: str, note: str = "", task: str = "classification"
    ) -> DatasetRecord:
        self._role_check()
        ts = time.time()
        if task not in ("classification", "detection"):
            task = "classification"
        record = DatasetRecord(
            id=None, name=name.strip() or "Dataset", task=task, created_ts=ts, note=note
        )
        record.id = self._store.add_dataset(record)
        if not enabled(self._storage_service):
            (paths.datasets_dir() / str(record.id) / "frames").mkdir(parents=True, exist_ok=True)
        if not self._config.active_dataset_id:
            self._config.active_dataset_id = record.id
        return record

    def delete_dataset(self, dataset_id: int) -> bool:
        self._role_check()
        if self._store.get_dataset(dataset_id) is None:
            return False
        for sample in self._store.list_samples(dataset_id, limit=1_000_000):
            if sample.id is not None and not self.delete_sample(sample.id):
                return False
        directory = paths.datasets_dir() / str(dataset_id)
        try:
            if directory.exists():
                shutil.rmtree(directory)
        except OSError:
            return False
        self._store.delete_dataset(dataset_id)
        if self._config.active_dataset_id == dataset_id:
            remaining = self._store.list_datasets()
            self._config.active_dataset_id = (remaining[0].id or 0) if remaining else 0
        return True

    def delete_sample(self, sample_id: int) -> bool:
        self._role_check()
        rec = self._store.get_sample(sample_id)
        if rec is None:
            return False
        if self._storage_service is not None and self._storage_service.catalog.aliases(
            "sample", str(sample_id)
        ):
            try:
                self._storage_service.delete_family("sample", str(sample_id))
            except StorageError:
                return False
            self._store.delete_sample(sample_id)
            return True
        for p in (rec.path, rec.thumb):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:  # pragma: no cover
                    return False
        self._store.delete_sample(sample_id)
        return True

    def set_annotations(
        self, sample_id: int, boxes: list[dict]
    ) -> list[SampleAnnotationRecord] | None:
        """Replace a detection sample's bounding boxes. Each box is
        ``{"label", "cx", "cy", "w", "h"}`` with coordinates normalized 0..1;
        they're clamped defensively so a bad drag can't store out-of-range geometry.
        Returns the stored boxes, or None if the sample doesn't exist."""
        self._role_check()
        if self._store.get_sample(sample_id) is None:
            return None
        ts = time.time()
        records: list[SampleAnnotationRecord] = []
        for box in boxes:
            label = str(box.get("label", "")).strip()
            if not label:
                continue
            cx = _clamp01(box.get("cx"))
            cy = _clamp01(box.get("cy"))
            w = _clamp01(box.get("w"))
            h = _clamp01(box.get("h"))
            if w <= 0 or h <= 0:
                continue
            records.append(
                SampleAnnotationRecord(
                    id=None,
                    sample_id=sample_id,
                    label=label,
                    cx=cx,
                    cy=cy,
                    w=w,
                    h=h,
                    created_ts=ts,
                )
            )
        sample = self._store.get_sample(sample_id)
        artifact = self._publish_annotation(sample_id, sample.label if sample else None, records)
        self._store.replace_annotations(sample_id, records)
        if artifact is not None:
            alias(self._storage_service, "sample", sample_id, "annotation", artifact)
        return self._store.list_annotations(sample_id)

    def _publish_annotation(
        self, sample_id: int, label: str | None, records: list, admission: Any = None
    ) -> Any:
        if admission is None and not enabled(self._storage_service):
            return None
        service = self._storage_service
        parent = service.catalog.resolve_alias("sample", str(sample_id), "file")
        sample = self._store.get_sample(sample_id)
        if sample is None:
            return None
        if parent is None and sample.path:
            parent = service.adopt_existing(
                Path(sample.path),
                "training_sample",
                namespace="sample",
                legacy_id=str(sample_id),
                variant="file",
                camera_id=sample.camera_id,
                metadata={"dataset_id": sample.dataset_id},
            )
        payload = json.dumps(
            {
                "sample_id": sample_id,
                "label": label,
                "boxes": [
                    {"label": row.label, "cx": row.cx, "cy": row.cy, "w": row.w, "h": row.h}
                    for row in records
                ],
                "updated_at": time.time(),
            },
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
        if len(payload) > 65536:
            raise StorageError("annotation_too_large", "Annotation exceeds the 64 KiB limit", 413)
        plan = admission or service.admit(
            "annotation", camera_id=sample.camera_id, expected_bytes=len(payload)
        )
        return service.put_bytes(
            "annotation",
            payload,
            admission=plan,
            camera_id=sample.camera_id,
            parent_id=parent.artifact_id if parent else None,
            metadata={"sample_id": sample_id, "dataset_id": sample.dataset_id},
            mime_type="application/json",
        )

    def relabel_sample(self, sample_id: int, label: str | None) -> DatasetSampleRecord | None:
        self._role_check()
        if self._store.get_sample(sample_id) is None:
            return None
        artifact = self._publish_annotation(
            sample_id, label, self._store.list_annotations(sample_id)
        )
        self._store.set_sample_label(sample_id, label)
        if artifact is not None:
            alias(self._storage_service, "sample", sample_id, "annotation", artifact)
        return self._store.get_sample(sample_id)

    def import_from_events(self, dataset_id: int, limit: int = 1000) -> int:
        """Add existing motion-event snapshots to a dataset as labeled samples.

        Idempotent: an event whose frame is already in the dataset is skipped,
        so clicking "Import" repeatedly never duplicates samples."""
        self._role_check()
        if self._store.get_dataset(dataset_id) is None:
            return 0
        if enabled(self._storage_service):
            return self._import_managed_events(dataset_id, limit)
        frames_dir = paths.datasets_dir() / str(dataset_id) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        added = 0
        for event in self._store.list_motion_events(limit=limit):
            if not event.thumb_path:
                continue
            src = Path(event.thumb_path)
            if not src.exists():
                continue
            ts = event.start_ts
            stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S-%f")[:-3]
            safe = safe_filename(event.camera_id)
            dest = frames_dir / f"evt_{safe}_{stamp}.jpg"
            if dest.exists():
                continue  # already imported on a previous click
            try:
                shutil.copyfile(src, dest)
            except OSError:  # pragma: no cover
                continue
            self._store.add_sample(
                DatasetSampleRecord(
                    id=None,
                    dataset_id=dataset_id,
                    path=str(dest),
                    thumb=str(dest),
                    label=event.label,
                    source="motion",
                    camera_id=event.camera_id,
                    host=self._host,
                    created_ts=ts,
                    confidence=event.confidence,
                )
            )
            added += 1
        return added

    def _import_managed_events(self, dataset_id: int, limit: int) -> int:
        service = self._storage_service
        added = 0
        for event in self._store.list_motion_events(limit=limit):
            import_id = f"{dataset_id}:{event.id}"
            imported = service.catalog.resolve_alias("event_import", import_id, "file")
            if imported is not None and imported.state != "deleted":
                continue
            artifact = service.catalog.resolve_alias("motion", str(event.id), "thumbnail")
            lease = None
            try:
                if artifact is not None:
                    lease = service.workspace_for_artifact(
                        artifact.artifact_id,
                        max_bytes=service.get_policy().workspace_max_bytes // 4,
                    )
                    source = service.materialize(artifact.artifact_id, lease)
                elif event.thumb_path and Path(event.thumb_path).is_file():
                    source = Path(event.thumb_path)
                else:
                    continue
                if source.stat().st_size > min(
                    service.get_policy().artifact_max_bytes, 16 * 1024 * 1024
                ):
                    raise StorageError(
                        "image_too_large", "Motion evidence exceeds the image import limit"
                    )
                image = cv2.imread(str(source))
                if image is None:
                    continue
                sample_id = self._save_sample(
                    dataset_id, event.camera_id, image, "motion", event.label, event.confidence
                )
                sample_artifact = service.catalog.resolve_alias("sample", str(sample_id), "file")
                if sample_artifact is not None:
                    service.catalog.alias(
                        "event_import", import_id, "file", sample_artifact.artifact_id
                    )
                added += 1
            finally:
                if lease is not None:
                    lease.release()
        return added

    # -- models ------------------------------------------------------------
    def register_byo(
        self, name: str, path: str, task: str = "classification"
    ) -> ModelRecord | None:
        self._analysis_check()
        p = Path(path).expanduser()
        if not p.exists():
            return None
        record = ModelRecord(
            id=None,
            name=name.strip() or p.stem,
            kind="byo",
            path=str(p),
            classes_json="[]",
            base_model="",
            metrics_json="{}",
            created_ts=time.time(),
            task="detection" if task == "detection" else "classification",
        )
        record.id = self._store.add_model(record)
        return record

    def activate_model(self, model_id: int | None) -> None:
        self._analysis_check()
        if model_id is not None and self._storage_service is not None:
            from tailcam.training.inference import ManagedModelLease

            record = self._store.get_model(model_id)
            if record is not None:
                candidate = ManagedModelLease(self._storage_service)
                try:
                    candidate.resolve(record)
                finally:
                    candidate.close()
        self._store.set_active_model(model_id)
        self._config.active_model_id = model_id or 0

    def delete_model(self, model_id: int) -> bool:
        self._role_check()
        rec = self._store.get_model(model_id)
        if rec is None or rec.kind == "base":
            return False  # never delete the base entry
        if self._storage_service is not None and self._storage_service.catalog.aliases(
            "model", str(model_id)
        ):
            try:
                self._storage_service.delete_family("model", str(model_id))
            except StorageError:
                return False
            self._store.delete_model(model_id)
            if self._config.active_model_id == model_id:
                self._config.active_model_id = 0
            return True
        # Only remove the artifact if it's one we manage (under models_dir).
        if rec.path:
            try:
                p = Path(rec.path)
                if paths.models_dir() in p.parents:
                    shutil.rmtree(p.parent, ignore_errors=True)
            except OSError:  # pragma: no cover
                pass
        self._store.delete_model(model_id)
        if self._config.active_model_id == model_id:
            self._config.active_model_id = 0
        return True

    # -- training runs -----------------------------------------------------
    def train(
        self,
        dataset_id: int,
        base_model: str | None = None,
        epochs: int | None = None,
        image_size: int | None = None,
    ) -> TrainingRunRecord | None:
        if self._job_service is None:
            self._role_check()
        dataset = self._store.get_dataset(dataset_id)
        if dataset is None:
            return None
        if self.has_active_run():
            raise RuntimeError(
                "a training run is already in progress — stop it or wait for it to finish"
            )
        cfg = self._config
        task = "detection" if dataset.task == "detection" else "classification"
        if task == "detection":
            base = base_model or cfg.detect_base_model
            imgsz = image_size or cfg.detect_image_size
        else:
            base = base_model or cfg.base_model
            imgsz = image_size or cfg.image_size
        ep = epochs or cfg.epochs
        if self._job_service is not None:
            return self._submit_training(dataset_id, base, ep, imgsz)
        storage_job = (
            ProducerWorkspace(self._storage_service, ("model_output", "export"))
            if enabled(self._storage_service)
            else None
        )
        resolved_base = base
        if storage_job is not None:
            try:
                resolved_base = self._bounded_training_base(storage_job, base)
            except Exception:
                storage_job.release()
                raise
        run = TrainingRunRecord(
            id=None,
            dataset_id=dataset_id,
            model_id=None,
            base_model=base,
            status="queued",
            params_json=json.dumps({"epochs": ep, "image_size": imgsz, "task": task}),
            metrics_json="{}",
            log="",
            epochs=ep,
            epoch=0,
            created_ts=time.time(),
        )
        run.id = self._store.add_run(run)
        stop = threading.Event()
        if storage_job is not None:
            self._storage_jobs[run.id] = storage_job
        with self._lock:
            self._run_stops[run.id] = stop
        threading.Thread(
            target=self._train_job,
            args=(run.id, dataset_id, resolved_base, ep, imgsz, task, stop),
            name=f"training-run-{run.id}",
            daemon=True,
        ).start()
        return self._store.get_run(run.id)

    def _bounded_training_base(self, job: ProducerWorkspace, base: str) -> str:
        if base.startswith("model:") and base[6:].isdecimal():
            artifact = job.service.catalog.resolve_alias("model", str(int(base[6:])), "file")
            if artifact is None or artifact.metadata.get("format") == "directory-zip":
                raise StorageError(
                    "unsupported_cache_control", "Select an existing YOLO weights artifact"
                )
            return str(job.service.materialize(artifact.artifact_id, job.lease))
        source = Path(base).expanduser()
        if source.is_symlink() or not source.is_file() or source.suffix.lower() != ".pt":
            raise StorageError(
                "unsupported_cache_control",
                "Unified training requires preprovisioned .pt weights: use an existing file "
                "or model:<id>. Automatic dependency downloads need an isolated worker.",
            )
        target = job.path / "base.pt"
        bounded_copy(job, source, target)
        return str(target)

    def has_active_run(self) -> bool:
        """True while any run is queued/preparing/training — one GPU, one run."""
        return any(r.status in ("queued", "preparing", "training") for r in self._store.list_runs())

    def stop_run(self, run_id: int) -> bool:
        if self._job_service is not None:
            run = self._store.get_run(run_id)
            job_id = json.loads(run.params_json).get("job_id") if run is not None else None
            if job_id:
                self.project_job(self._job_service.request_cancel(job_id))
                return True
        with self._lock:
            stop = self._run_stops.get(run_id)
        if stop is None:
            return False
        stop.set()
        return True

    def dataset_revision(self, dataset_id: int) -> str:
        from tailcam.workloads.training import dataset_revision

        return dataset_revision(self, dataset_id)

    def dataset_review(self, dataset_id: int) -> dict:
        from tailcam.workloads.training import dataset_review

        return dataset_review(self, dataset_id)

    def prepare_training_job(self, **kwargs):
        from tailcam.workloads.training import prepare_training_job

        with self._job_lock:
            return prepare_training_job(self, **kwargs)

    def project_job(self, job) -> None:
        from tailcam.workloads.training import project_job

        with self._job_lock:
            project_job(self, job)

    def reconcile_jobs(self) -> None:
        if self._job_service is None:
            return
        for run in self._store.list_runs():
            job_id = json.loads(run.params_json).get("job_id")
            if job_id:
                record = self._job_service.get(job_id)
                if record is not None:
                    self.project_job(record)

    def _submit_training(self, dataset_id: int, base: str, epochs: int, imgsz: int):
        from uuid import uuid4

        from tailcam.jobs.models import JobError

        if base.startswith("model:") and base[6:].isdecimal():
            model_id = int(base[6:])
        else:
            model = next((m for m in self._store.list_models()
                          if m.path == base or m.base_model == base or m.name == base), None)
            if model is None or model.id is None:
                raise JobError(
                    "model_unavailable", "Register existing model weights before training."
                )
            model_id = model.id
        plan = self._job_service.placement.plan("training")
        if plan.selected_target.node_id is None:
            raise JobError("worker_unavailable", "Training requires a TailCam worker.")
        review = self.dataset_review(dataset_id)
        spec = self.prepare_training_job(
            dataset_id=dataset_id, dataset_revision=review["revision"], base_model_id=model_id,
            epochs=epochs, image_size=imgsz, seed=1234,
            worker_node_id=plan.selected_target.node_id, camera_ids=review["camera_ids"],
            classes=review["classes"], job_id=str(uuid4()), budget=plan.budget,
        )
        self._job_service.submit(
            spec, idempotency_key=spec.job_id, principal_scope="manual-training"
        )
        return self._store.get_run(int(spec.reference["training_run_id"]))

    def _train_job(
        self,
        run_id: int,
        dataset_id: int,
        base: str,
        epochs: int,
        imgsz: int,
        task: str,
        stop: threading.Event,
    ) -> None:
        from tailcam.training import runner
        from tailcam.training.engine import engine_available, torch_device

        try:
            if not engine_available():
                self._store.update_run(
                    run_id,
                    status="error",
                    ended_ts=time.time(),
                    log="Training engine not installed (pip install 'tailcam[training]').",
                )
                return
            self._store.update_run(run_id, status="preparing", started_ts=time.time())
            job = self._storage_jobs.get(run_id)
            run_dir = job.path if job is not None else paths.models_dir() / f"run-{run_id}"
            data_dir = run_dir / "dataset"
            export_options: dict[str, Any] = {}
            if job is not None:
                export_options = {
                    "sample_resolver": lambda sample: sample_path(job, sample),
                    "copy_sample": lambda source, target: bounded_copy(job, source, target),
                }
            if task == "detection":
                classes, n_train, n_val = runner.export_detection_dataset(
                    self._store, dataset_id, data_dir, **export_options
                )
            else:
                classes, n_train, n_val = runner.export_classification_dataset(
                    self._store, dataset_id, data_dir, **export_options
                )
            if job is not None:
                export = archive_tree(job, data_dir, job.path / "dataset.zip")
                artifact = job.finish(
                    export,
                    "export",
                    mime_type="application/zip",
                    metadata={
                        "run_id": run_id,
                        "dataset_id": dataset_id,
                        "filename": "dataset.zip",
                    },
                )
                alias(job.service, "training_run", run_id, "export", artifact)
            self._store.update_run(
                run_id,
                status="training",
                log=f"{n_train} train / {n_val} val · classes: {', '.join(classes)}",
            )
            train_options = {"offline": True} if job is not None else {}
            result = runner.train_model(
                base,
                data_dir,
                epochs,
                imgsz,
                torch_device(),
                run_dir,
                on_epoch=lambda e: self._training_epoch(run_id, e, job),
                should_stop=stop.is_set,
                task=task,
                **train_options,
            )
            if stop.is_set():
                self._store.update_run(run_id, status="stopped", ended_ts=time.time())
                return
            metrics = result.get("metrics", {})
            artifact = None
            model_path = result["model_path"]
            if job is not None:
                artifact = job.finish(
                    Path(model_path),
                    "model_output",
                    metadata={"run_id": run_id, "filename": Path(model_path).name},
                )
                model_path = local_path(job.service, artifact)
            run_record = self._store.get_run(run_id)
            model_id = self._store.add_model(
                ModelRecord(
                    id=None,
                    name=f"Trained {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    kind="trained",
                    path=model_path,
                    classes_json=json.dumps(classes),
                    base_model=(run_record.base_model if run_record is not None else base),
                    metrics_json=json.dumps(metrics),
                    created_ts=time.time(),
                    task=task,
                )
            )
            if job is not None and artifact is not None:
                alias(job.service, "model", model_id, "file", artifact)
                job.release()
                self._storage_jobs.pop(run_id, None)
            self._store.update_run(
                run_id,
                status="complete",
                model_id=model_id,
                epoch=epochs,
                metrics_json=json.dumps(metrics),
                ended_ts=time.time(),
            )
            log.info("training run %s complete -> model %s", run_id, model_id)
            self._notify_run(run_id, dataset_id, "complete", model_id, metrics)
        except Exception as exc:
            log.exception("training run %s failed: %s", run_id, exc)
            self._store.update_run(run_id, status="error", log=str(exc)[:500], ended_ts=time.time())
            self._notify_run(run_id, dataset_id, "error", None, None)
        finally:
            with self._lock:
                self._run_stops.pop(run_id, None)

    def _training_epoch(self, run_id: int, epoch: int, job: ProducerWorkspace | None) -> None:
        if job is not None:
            job.check()
        self._store.update_run(run_id, epoch=epoch)

    def _notify_run(
        self,
        run_id: int,
        dataset_id: int,
        status: str,
        model_id: int | None,
        metrics: dict | None,
    ) -> None:
        if self._notifier is None:
            return
        try:
            self._notifier.notify_training(
                run_id=run_id,
                dataset_id=dataset_id,
                status=status,
                model_id=model_id,
                metrics=metrics,
            )
        except Exception as exc:  # never let notification break training
            log.debug("training notification failed: %s", exc)


def _clamp01(value: object) -> float:
    try:
        return min(1.0, max(0.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _write_thumb(image: np.ndarray, dataset_id: int, stem: str) -> Path | None:
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
