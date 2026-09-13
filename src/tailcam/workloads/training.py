"""Immutable dataset snapshots and projections into the existing training API."""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from tailcam.jobs.models import (
    ArtifactRef,
    JobError,
    JobRecord,
    JobSpec,
    OutputSlot,
    PlacementPlan,
    ResourceBudget,
    WorkerTarget,
)
from tailcam.media.storage import alias, local_path
from tailcam.persistence.models import ModelRecord, TrainingRunRecord
from tailcam.training.labels import validate_class_label
from tailcam.workloads.executor import validate_budget


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class DatasetSnapshot:
    def __init__(self, training: Any, dataset_id: int) -> None:
        self.training = training
        self.dataset = training._store.get_dataset(dataset_id)
        if self.dataset is None:
            raise JobError("dataset_missing", "Dataset does not exist.", 404)
        self.samples = sorted(
            training._store.list_samples(dataset_id, limit=10001), key=lambda sample: sample.id or 0
        )
        if len(self.samples) > 10000:
            raise JobError(
                "dataset_too_large", "A training snapshot supports at most 10000 samples."
            )
        self.annotations = {
            sample.id: training._store.list_annotations(sample.id) for sample in self.samples
        }

    def list_samples(self, _dataset_id: int, limit: int = 10000) -> list:
        return self.samples[:limit]

    def list_annotations(self, sample_id: int) -> list:
        return self.annotations.get(sample_id, [])

    def revision(self) -> str:
        items = []
        service = self.training._storage_service
        for sample in self.samples:
            artifact = (
                service.catalog.resolve_alias("sample", str(sample.id), "file") if service else None
            )
            if artifact is not None:
                image = [artifact.sha256, artifact.size_bytes]
            else:
                path = Path(sample.path)
                if path.is_symlink() or not path.is_file():
                    raise JobError("sample_unavailable", "A dataset sample is missing or unsafe.")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while data := stream.read(1024 * 1024):
                        digest.update(data)
                image = [digest.hexdigest(), path.stat().st_size]
            row = asdict(sample)
            row.pop("path")
            row.pop("thumb")
            items.append(
                {
                    "sample": row,
                    "image": image,
                    "annotations": [asdict(box) for box in self.annotations[sample.id]],
                }
            )
        return hashlib.sha256(
            _canonical({"dataset": asdict(self.dataset), "samples": items})
        ).hexdigest()


def dataset_revision(training: Any, dataset_id: int) -> str:
    return DatasetSnapshot(training, dataset_id).revision()


def prepare_training_job(
    training: Any,
    *,
    dataset_id: int,
    dataset_revision: str,
    base_model_id: int,
    epochs: int,
    image_size: int,
    seed: int,
    worker_node_id: str,
    camera_ids: list[str],
    classes: list[str],
    job_id: str,
    budget: ResourceBudget,
    supervision_id: str = "",
    experiment_id: str = "",
    max_attempts: int = 2,
) -> JobSpec:
    from tailcam.training import runner

    validate_budget(budget)
    job_id = str(UUID(job_id))
    service = training._storage_service
    if service is None:
        raise JobError("storage_unavailable", "Artifact storage is required for durable training.")
    for label in classes:
        validate_class_label(label)
    if not classes or not camera_ids:
        raise JobError(
            "invalid_scope", "Select permitted cameras and classes before training.", 422
        )
    request = {
        "dataset_id": dataset_id,
        "dataset_revision": dataset_revision,
        "base_model_id": base_model_id,
        "epochs": epochs,
        "image_size": image_size,
        "seed": seed,
        "worker_node_id": worker_node_id,
        "camera_ids": sorted(camera_ids),
        "classes": sorted(classes),
        "budget": budget.model_dump(mode="json"),
        "supervision_id": supervision_id,
        "experiment_id": experiment_id,
        "max_attempts": max_attempts,
    }
    fingerprint = hashlib.sha256(_canonical(request)).hexdigest()
    saved = service.catalog.setting(f"training_spec:{job_id}")
    if saved:
        entry = json.loads(saved)
        if entry["request_hash"] != fingerprint:
            raise JobError("idempotency_conflict", "Training job ID belongs to another request.")
        return JobSpec.model_validate(entry["spec"])
    snapshot = DatasetSnapshot(training, dataset_id)
    if snapshot.revision() != dataset_revision:
        raise JobError("dataset_changed", "Dataset changed since this experiment was approved.")
    allowed = set(classes)
    snapshot.samples = [sample for sample in snapshot.samples if sample.camera_id in camera_ids]
    if snapshot.dataset.task == "detection":
        for sample in snapshot.samples:
            boxes = snapshot.annotations[sample.id]
            if any(box.label not in allowed for box in boxes):
                # Exclude the whole image rather than turn an unapproved object into background.
                snapshot.annotations[sample.id] = []
        snapshot.samples = [
            sample for sample in snapshot.samples if snapshot.annotations[sample.id]
        ]
    else:
        snapshot.samples = [sample for sample in snapshot.samples if sample.label in allowed]
    if not snapshot.samples:
        raise JobError("empty_scope", "No samples match the approved camera/class subset.")
    from tailcam.workloads.models import registered_artifact

    model, model_artifact = registered_artifact(service, training._store, base_model_id)
    if model_artifact.state not in {"committed", "replicated"}:
        raise JobError("model_unavailable", "The selected model is not committed at its owner.")
    if model.base_model in {"florence2", "qwen2.5-vl"}:
        backend = model.base_model
    else:
        backend = "yolo"
    policy = service.get_policy().model_copy(deep=True)
    export_plan = service.admit("export", policy_snapshot=policy, requires_workspace=True)
    model_plan = service.admit("model_output", policy_snapshot=policy)
    lease = service.workspace(
        "export",
        max_bytes=min(budget.workspace_bytes, policy.workspace_max_bytes // 4),
        admission=export_plan,
    )
    try:
        frozen_refs = {}
        for sample in snapshot.samples:
            artifact = service.catalog.resolve_alias("sample", str(sample.id), "file")
            if artifact is None:
                artifact = service.adopt_existing(
                    sample.path,
                    "training_sample",
                    namespace="sample",
                    legacy_id=str(sample.id),
                    variant="file",
                    camera_id=sample.camera_id,
                )
            frozen_refs[sample.id] = ArtifactRef.from_artifact(artifact)

        def resolve_sample(sample):
            return service.materialize_ref(frozen_refs[sample.id], lease)

        def copy_sample(source, target):
            size = Path(source).stat().st_size
            if lease.check() + size > lease.max_bytes:
                raise JobError("workspace_full", "Dataset export exceeds its scratch reservation.")
            import shutil

            shutil.copyfile(source, target)

        data_dir = lease.path / "dataset"
        options: dict[str, Any] = {
            "sample_resolver": resolve_sample,
            "copy_sample": copy_sample,
            "split_seed": seed,
        }
        if backend != "yolo":
            from tailcam.activelearning.annotations import (
                FrameAnnotation,
                to_florence_od_string,
                to_qwen_json,
            )

            data_dir.mkdir()
            sample_manifest = []
            for index, sample in enumerate(snapshot.samples):
                target = data_dir / f"{index:06d}.jpg"
                copy_sample(resolve_sample(sample), target)
                annotations = [
                    FrameAnnotation(
                        label=box.label,
                        cx=box.cx,
                        cy=box.cy,
                        w=box.w,
                        h=box.h,
                        confidence=1.0,
                    )
                    for box in snapshot.annotations[sample.id]
                ]
                if backend == "florence2":
                    text = to_florence_od_string(annotations)
                else:
                    import cv2

                    image = cv2.imread(str(target))
                    if image is None:
                        raise JobError("sample_unavailable", "Sample image could not be decoded.")
                    text = to_qwen_json(annotations, image.shape[1], image.shape[0])
                sample_manifest.append({"image": target.name, "text": text})
            (data_dir / "samples.json").write_bytes(_canonical(sample_manifest))
            used_classes, count_train, count_val = sorted(classes), len(sample_manifest), 0
        elif snapshot.dataset.task == "detection":
            used_classes, count_train, count_val = runner.export_detection_dataset(
                snapshot,  # type: ignore[arg-type]
                dataset_id,
                data_dir,
                **options,
            )
        else:
            used_classes, count_train, count_val = runner.export_classification_dataset(
                snapshot,  # type: ignore[arg-type]
                dataset_id,
                data_dir,
                **options,
            )
        archive = lease.path / "dataset.zip"
        # Repeated preparation produces identical archive bytes, including ZIP timestamps.
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as output:
            for path in sorted(data_dir.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    if path.is_symlink():
                        raise JobError("unsafe_export", "Dataset export contains a symbolic link.")
                    continue
                placeholder = (
                    b"# Rebuilt by the worker from frozen class metadata.\n"
                    if path.name == "data.yaml"
                    else None
                )
                size = len(placeholder) if placeholder else path.stat().st_size
                if lease.check() + size + 1024 > lease.max_bytes:
                    raise JobError("workspace_full", "Dataset archive exceeds its scratch budget.")
                info = zipfile.ZipInfo(path.relative_to(data_dir).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100600 << 16
                with output.open(info, "w") as dest:
                    if placeholder:
                        dest.write(placeholder)
                    else:
                        copied = 0
                        with path.open("rb") as source:
                            while chunk := source.read(65536):
                                copied += len(chunk)
                                if copied > size or lease.check() + len(chunk) > lease.max_bytes:
                                    raise JobError(
                                        "workspace_full", "Dataset changed size during export."
                                    )
                                dest.write(chunk)
        current = DatasetSnapshot(training, dataset_id)
        if current.revision() != dataset_revision:
            raise JobError("dataset_changed", "Dataset changed while preparing the experiment.")
        artifact_id = str(uuid5(UUID(job_id), "dataset-export"))
        export = service.finalize(
            archive,
            "export",
            admission=export_plan,
            artifact_id=artifact_id,
            mime_type="application/zip",
            metadata={
                "dataset_id": dataset_id,
                "dataset_revision": dataset_revision,
                "job_id": job_id,
                "seed": seed,
            },
        )
        if export.state not in {"committed", "replicated"}:
            raise JobError(
                "input_unavailable", "Dataset export has not reached its selected owner."
            )
        target = WorkerTarget(node_id=worker_node_id)
        spec = JobSpec(
            job_id=job_id,
            task="training",
            origin_node_id=service.node_id,
            parameters={
                "backend": backend,
                "task": snapshot.dataset.task,
                "epochs": epochs,
                "image_size": image_size,
                "seed": seed,
                "classes": used_classes,
                "model_format": model_artifact.metadata.get("format", "weights"),
            },
            input_artifacts=[
                ArtifactRef.from_artifact(export, "dataset"),
                ArtifactRef.from_artifact(model_artifact, "model"),
            ],
            outputs=[OutputSlot(slot="model", kind="model_output", admission=model_plan)],
            placement_plan=PlacementPlan(
                task="training", requested_target=target, selected_target=target, budget=budget
            ),
            resource_budget=budget,
            deadline_at=time.time() + budget.wall_seconds,
            max_attempts=max_attempts,
            priority=10,
            reference={
                "dataset_id": str(dataset_id),
                "dataset_revision": dataset_revision,
                "supervision_id": supervision_id,
                "experiment_id": experiment_id,
            },
        )
        params = {
            **spec.parameters,
            "job_id": job_id,
            "dataset_revision": dataset_revision,
            "train_samples": count_train,
            "validation_samples": count_val,
            "supervision_id": supervision_id,
            "experiment_id": experiment_id,
        }
        existing = next(
            (
                run
                for run in training._store.list_runs()
                if json.loads(run.params_json).get("job_id") == job_id
            ),
            None,
        )
        if existing is None:
            run_id = training._store.add_run(
                TrainingRunRecord(
                    id=None,
                    dataset_id=dataset_id,
                    model_id=None,
                    base_model=f"model:{base_model_id}",
                    status="queued",
                    params_json=json.dumps(params),
                    metrics_json="{}",
                    log="",
                    epochs=epochs,
                    epoch=0,
                    created_ts=time.time(),
                )
            )
        else:
            run_id = existing.id
        spec.reference["training_run_id"] = str(run_id)
        service.catalog.set_setting(
            f"training_spec:{job_id}",
            json.dumps(
                {
                    "request_hash": fingerprint,
                    "spec": spec.model_dump(mode="json"),
                }
            ),
        )
        alias(service, "training_run", run_id, "export", export)
        return spec
    finally:
        lease.release()


def dataset_review(training: Any, dataset_id: int) -> dict:
    snapshot = DatasetSnapshot(training, dataset_id)
    return {
        "dataset_id": dataset_id,
        "revision": snapshot.revision(),
        "task": snapshot.dataset.task,
        "camera_ids": sorted({sample.camera_id for sample in snapshot.samples}),
        "classes": sorted(
            {box.label for boxes in snapshot.annotations.values() for box in boxes}
            if snapshot.dataset.task == "detection"
            else {sample.label for sample in snapshot.samples if sample.label}
        ),
    }


def project_job(training: Any, job: JobRecord) -> None:
    run_text = job.reference.get("training_run_id")
    if not run_text or not run_text.isdecimal():
        return
    run_id = int(run_text)
    run = training._store.get_run(run_id)
    if run is None:
        return
    stage = job.stages[-1] if job.stages else None
    state = {
        "queued": "queued",
        "waiting_for_worker": "queued",
        "leased": "preparing",
        "running": "training",
        "committing": "training",
        "retry_wait": "queued",
        "succeeded": "complete",
        "failed": "error",
        "deadline_exceeded": "error",
        "cancelled": "stopped",
        "cancel_requested": "training",
    }[job.state]
    changes: dict[str, Any] = {
        "status": state,
        "started_ts": job.started_at,
        "ended_ts": job.ended_at,
    }
    if stage is not None:
        if stage.progress.epoch is not None:
            changes["epoch"] = stage.progress.epoch
        if stage.error is not None:
            changes["log"] = stage.error.detail
    if job.state == "cancel_requested":
        changes["log"] = "Stop requested; waiting for worker process termination."
    if state == "complete" and stage is not None:
        output = next((ref for ref in stage.outputs if ref.slot == "model"), None)
        if output is None:
            raise JobError("invalid_result", "Training completed without model output.")
        artifact = training._storage_service.catalog.get(output.artifact_id)
        if artifact is None:
            return  # remote commit metadata is reconciled before projecting a model
        model_id = run.model_id
        if model_id is None:
            name = f"Training job {job.job_id}"
            model = next((m for m in training._store.list_models() if m.name == name), None)
            model_id = (
                model.id
                if model is not None
                else training._store.add_model(
                    ModelRecord(
                        id=None,
                        name=name,
                        kind="trained",
                        path=local_path(training._storage_service, artifact),
                        classes_json=json.dumps(stage.result.get("classes", [])),
                        base_model=(
                            str(stage.result["backend"])
                            if stage.result.get("backend") in {"florence2", "qwen2.5-vl"}
                            else run.base_model
                        ),
                        metrics_json=json.dumps(stage.result.get("metrics", {})),
                        created_ts=time.time(),
                        task=json.loads(run.params_json).get("task", "classification"),
                    )
                )
            )
        alias(training._storage_service, "model", model_id, "file", artifact)
        changes.update(
            model_id=model_id,
            epoch=run.epochs,
            metrics_json=json.dumps(stage.result.get("metrics", {})),
        )
    previous_state = run.status
    training._store.update_run(run_id, **changes)
    if state in {"complete", "error", "stopped"} and previous_state != state:
        training._notify_run(
            run_id,
            run.dataset_id,
            state,
            changes.get("model_id"),
            stage.result.get("metrics", {}) if stage else {},
        )
