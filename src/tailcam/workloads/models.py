"""Resolve registered models or select fixed worker-owned built-in provisioning."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import threading
from pathlib import Path
from uuid import uuid4

from tailcam.jobs.models import ArtifactRef, JobError

_CACHE_LOCK = threading.Lock()
_BUILTIN_FILES = {
    "yolo11n": [("yolo11n.pt", 1_000_000, 16_000_000)],
    "yolov4-tiny": [
        ("yolov4-tiny.cfg", 100, 128_000),
        ("yolov4-tiny.weights", 20_000_000, 32_000_000),
    ],
}


def persist_builtin(workspace: Path, builtin: str) -> None:
    """Retain fixed built-in assets only after the isolated engine answered.

    This finite runtime cache uses the existing built-in directory, never a
    user-selected output path. Each file publishes atomically, without loading
    weights or importing a model library into the camera/API process.
    """
    from tailcam.ai.detector import builtin_models_dir

    entries = _BUILTIN_FILES.get(builtin)
    if entries is None:
        return
    with _CACHE_LOCK:
        root = builtin_models_dir()
        if any(part.is_symlink() for part in (root, *root.parents)):
            raise JobError("unsafe_cache", "Built-in cache must not contain symbolic links.")
        for filename, minimum, maximum in entries:
            source = workspace / filename
            if source.is_symlink() or not source.is_file():
                raise JobError("unsafe_cache", "Built-in worker output is not a regular file.")
            if not minimum <= source.stat().st_size <= maximum:
                raise JobError("unsafe_cache", "Built-in worker output exceeds its fixed bound.")
        root.mkdir(parents=True, exist_ok=True)
        for filename, minimum, maximum in entries:
            source, target = workspace / filename, root / filename
            if target.is_symlink():
                raise JobError("unsafe_cache", "Built-in cache target is a symbolic link.")
            temporary = root / f".{filename}.{uuid4().hex}.tmp"
            try:
                written = 0
                with source.open("rb") as inp, temporary.open("xb") as out:
                    while chunk := inp.read(65536):
                        written += len(chunk)
                        if written > maximum:
                            raise JobError("unsafe_cache", "Built-in worker output changed size.")
                        out.write(chunk)
                    if written < minimum:
                        raise JobError("unsafe_cache", "Built-in worker output was truncated.")
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)


def registered_artifact(storage, store, model_id: int):
    model = store.get_model(model_id)
    if model is None:
        raise JobError("model_unavailable", "Registered model does not exist.")
    artifact = storage.catalog.resolve_alias("model", str(model_id), "file")
    if artifact is None:
        path = Path(model.path).expanduser()
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".pt":
            raise JobError(
                "model_unavailable", "Registered model needs existing regular .pt weights."
            )
        artifact = storage.adopt_existing(
            path,
            "model_output",
            namespace="model",
            legacy_id=str(model_id),
            variant="file",
            metadata={"filename": path.name},
        )
    return model, artifact


def local_model_inputs(
    storage, store, config, selector: str, lease, task: str, *, deadline_at=None
) -> tuple:
    model_id = (
        int(selector[6:])
        if selector.startswith("model:") and selector[6:].isdecimal()
        else config.training.active_model_id
        if selector != "builtin"
        else None
    )
    record = store.get_model(model_id) if model_id else None
    if selector.startswith("backend:"):
        backend = selector.partition(":")[2]
        record = next((model for model in store.list_models() if model.base_model == backend), None)
        if record is None:
            raise JobError("model_unavailable", "Register the selected vision model on its worker.")
    if record and task == "live_detection" and record.task != "detection":
        if selector.startswith("model:"):
            raise JobError(
                "model_task_mismatch", "Selected model does not produce detection boxes."
            )
        record = None
    if record is not None:
        record, artifact = registered_artifact(storage, store, record.id)
        path = storage.materialize_ref(
            ArtifactRef.from_artifact(artifact), lease, deadline_at=deadline_at
        )
        backend = (
            record.base_model
            if record.base_model in {"florence2", "qwen2.5-vl"}
            else "classifier"
            if record.task == "classification"
            else "yolo"
        )
        return {
            "_model_name": record.name,
            "backend": backend,
            "classes": json.loads(record.classes_json),
            "confidence": config.training.detect_conf,
            "model_format": artifact.metadata.get("format", "weights"),
        }, [{"slot": "model", "path": path.relative_to(lease.path).as_posix()}]
    if selector.startswith("model:"):
        raise JobError("model_unavailable", "Selected worker model does not exist.")
    from tailcam.ai.detector import builtin_models_dir

    def copy(source: Path, slot: str) -> dict:
        if source.is_symlink() or not source.is_file():
            raise JobError("model_unavailable", "Provision the selected worker model before use.")
        if lease.check() + source.stat().st_size > lease.max_bytes:
            raise JobError("workspace_full", "Preprovisioned model exceeds the scratch budget.")
        target = lease.path / source.name
        shutil.copyfile(source, target)
        return {"slot": slot, "path": target.name}

    opencv = config.detection.engine == "opencv" or (
        config.detection.engine == "auto" and importlib.util.find_spec("ultralytics") is None
    )
    if opencv:
        if not all(
            (builtin_models_dir() / name).is_file()
            for name in ("yolov4-tiny.weights", "yolov4-tiny.cfg")
        ):
            return {
                "backend": "opencv",
                "builtin": "yolov4-tiny",
                "confidence": config.detection.confidence,
                "_model_name": "yolov4-tiny",
            }, []
        inputs = [
            copy(builtin_models_dir() / "yolov4-tiny.weights", "model"),
            copy(builtin_models_dir() / "yolov4-tiny.cfg", "model_config"),
        ]
        return {
            "backend": "opencv",
            "confidence": config.detection.confidence,
            "_model_name": "yolov4-tiny",
        }, inputs
    configured = config.detection.model or "yolo11n.pt"
    source = Path(configured).expanduser()
    if source.name == configured:
        source = builtin_models_dir() / configured
    if configured == "yolo11n.pt" and not source.is_file():
        return {
            "backend": "yolo",
            "builtin": "yolo11n",
            "confidence": config.detection.confidence,
            "_model_name": "yolo11n.pt",
        }, []
    return {
        "backend": "yolo",
        "confidence": config.detection.confidence,
        "_model_name": source.name,
    }, [copy(source, "model")]
