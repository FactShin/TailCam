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
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from tailcam import paths
from tailcam.camera.manager import CameraManager
from tailcam.config import TrainingConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.models import (
    DatasetRecord,
    DatasetSampleRecord,
    ModelRecord,
    SampleAnnotationRecord,
    TrainingRunRecord,
)
from tailcam.persistence.store import Store
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
    ) -> None:
        self._manager = manager
        self._store = store
        self._config = config
        self._analyzer = analyzer
        self._host = host
        self._notifier = notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.collected_this_session = 0
        self._run_stops: dict[int, threading.Event] = {}

    # -- startup -----------------------------------------------------------
    def startup(self) -> None:
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
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="training-collect", daemon=True
            )
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
        ts = time.time()
        stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        safe = camera_id.replace("/", "_") or "cam"
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
        ts = time.time()
        if task not in ("classification", "detection"):
            task = "classification"
        record = DatasetRecord(id=None, name=name.strip() or "Dataset", task=task,
                               created_ts=ts, note=note)
        record.id = self._store.add_dataset(record)
        (paths.datasets_dir() / str(record.id) / "frames").mkdir(parents=True, exist_ok=True)
        if not self._config.active_dataset_id:
            self._config.active_dataset_id = record.id
        return record

    def delete_dataset(self, dataset_id: int) -> bool:
        if self._store.get_dataset(dataset_id) is None:
            return False
        self._store.delete_dataset(dataset_id)
        shutil.rmtree(paths.datasets_dir() / str(dataset_id), ignore_errors=True)
        if self._config.active_dataset_id == dataset_id:
            remaining = self._store.list_datasets()
            self._config.active_dataset_id = (remaining[0].id or 0) if remaining else 0
        return True

    def delete_sample(self, sample_id: int) -> bool:
        rec = self._store.get_sample(sample_id)
        if rec is None:
            return False
        for p in (rec.path, rec.thumb):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:  # pragma: no cover
                    pass
        self._store.delete_sample(sample_id)
        return True

    def set_annotations(
        self, sample_id: int, boxes: list[dict]
    ) -> list[SampleAnnotationRecord] | None:
        """Replace a detection sample's bounding boxes. Each box is
        ``{"label", "cx", "cy", "w", "h"}`` with coordinates normalized 0..1;
        they're clamped defensively so a bad drag can't store out-of-range geometry.
        Returns the stored boxes, or None if the sample doesn't exist."""
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
                    id=None, sample_id=sample_id, label=label,
                    cx=cx, cy=cy, w=w, h=h, created_ts=ts,
                )
            )
        self._store.replace_annotations(sample_id, records)
        return self._store.list_annotations(sample_id)

    def import_from_events(self, dataset_id: int, limit: int = 1000) -> int:
        """Add existing motion-event snapshots to a dataset as labeled samples.

        Idempotent: an event whose frame is already in the dataset is skipped,
        so clicking "Import" repeatedly never duplicates samples."""
        if self._store.get_dataset(dataset_id) is None:
            return 0
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
            safe = event.camera_id.replace("/", "_") or "cam"
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

    # -- models ------------------------------------------------------------
    def register_byo(
        self, name: str, path: str, task: str = "classification"
    ) -> ModelRecord | None:
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
        self._store.set_active_model(model_id)
        self._config.active_model_id = model_id or 0

    def delete_model(self, model_id: int) -> bool:
        rec = self._store.get_model(model_id)
        if rec is None or rec.kind == "base":
            return False  # never delete the base entry
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
        with self._lock:
            self._run_stops[run.id] = stop
        threading.Thread(
            target=self._train_job,
            args=(run.id, dataset_id, base, ep, imgsz, task, stop),
            name=f"training-run-{run.id}",
            daemon=True,
        ).start()
        return self._store.get_run(run.id)

    def has_active_run(self) -> bool:
        """True while any run is queued/preparing/training — one GPU, one run."""
        return any(
            r.status in ("queued", "preparing", "training") for r in self._store.list_runs()
        )

    def stop_run(self, run_id: int) -> bool:
        with self._lock:
            stop = self._run_stops.get(run_id)
        if stop is None:
            return False
        stop.set()
        return True

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
                    run_id, status="error", ended_ts=time.time(),
                    log="Training engine not installed (pip install 'tailcam[training]').",
                )
                return
            self._store.update_run(run_id, status="preparing", started_ts=time.time())
            run_dir = paths.models_dir() / f"run-{run_id}"
            data_dir = run_dir / "dataset"
            if task == "detection":
                classes, n_train, n_val = runner.export_detection_dataset(
                    self._store, dataset_id, data_dir
                )
            else:
                classes, n_train, n_val = runner.export_classification_dataset(
                    self._store, dataset_id, data_dir
                )
            self._store.update_run(
                run_id, status="training",
                log=f"{n_train} train / {n_val} val · classes: {', '.join(classes)}",
            )
            result = runner.train_model(
                base, data_dir, epochs, imgsz, torch_device(), run_dir,
                on_epoch=lambda e: self._store.update_run(run_id, epoch=e),
                should_stop=stop.is_set,
                task=task,
            )
            if stop.is_set():
                self._store.update_run(run_id, status="stopped", ended_ts=time.time())
                return
            metrics = result.get("metrics", {})
            model_id = self._store.add_model(
                ModelRecord(
                    id=None,
                    name=f"Trained {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    kind="trained",
                    path=result["model_path"],
                    classes_json=json.dumps(classes),
                    base_model=base,
                    metrics_json=json.dumps(metrics),
                    created_ts=time.time(),
                    task=task,
                )
            )
            self._store.update_run(
                run_id, status="complete", model_id=model_id, epoch=epochs,
                metrics_json=json.dumps(metrics), ended_ts=time.time(),
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

    def _notify_run(
        self, run_id: int, dataset_id: int, status: str,
        model_id: int | None, metrics: dict | None,
    ) -> None:
        if self._notifier is None:
            return
        try:
            self._notifier.notify_training(
                run_id=run_id, dataset_id=dataset_id, status=status,
                model_id=model_id, metrics=metrics,
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
