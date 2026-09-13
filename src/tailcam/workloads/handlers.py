"""Allowlisted local engines. Inputs were materialized by the owning executor."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tailcam.workloads.process import ExecutionError

_ENGINE_CACHE: dict[str, tuple[Any, Any]] = {}


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EncodeParameters(Parameters):
    fps: int = Field(default=24, ge=1, le=240)
    target_fps: int = Field(default=60, ge=1, le=240)
    interpolate: bool = True
    deflicker: bool = True
    engine: Literal["ffmpeg", "rife"] = "ffmpeg"
    quality: Literal["standard", "high", "maximum"] = "high"


class InferenceParameters(Parameters):
    backend: Literal["yolo", "opencv", "classifier", "florence2", "qwen2.5-vl", "ollama"] = "yolo"
    confidence: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    classes: list[str] = Field(default_factory=list, max_length=1000)
    model_format: Literal["weights", "directory-zip"] = "weights"
    builtin: Literal["", "yolo11n", "yolov4-tiny"] = ""


def remaining_bytes(root: Path, maximum: int) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ExecutionError("invalid_request", "Workspace contains a symbolic link.")
        if path.is_file():
            total += path.stat().st_size
    return max(0, maximum - total)


def _provision_builtin(name: str, root: Path, maximum: int) -> list[dict]:
    """Download only fixed built-ins, inside this child's monitored workspace.

    The owning process deadline also bounds TLS/DNS and stalled downloads. No
    job parameter supplies a URL, filesystem destination, or executable code.
    """
    import httpx

    from tailcam.ai.detector import (
        _ULTRALYTICS_ASSETS,
        _YOLO4_CFG_URLS,
        _YOLO4_WEIGHTS_MIN_BYTES,
        _YOLO4_WEIGHTS_URLS,
    )

    files = (
        [("model", "yolo11n.pt", [_ULTRALYTICS_ASSETS + "yolo11n.pt"], 1_000_000, 16_000_000)]
        if name == "yolo11n"
        else [
            ("model_config", "yolov4-tiny.cfg", _YOLO4_CFG_URLS, 100, 128_000),
            (
                "model",
                "yolov4-tiny.weights",
                _YOLO4_WEIGHTS_URLS,
                _YOLO4_WEIGHTS_MIN_BYTES,
                32_000_000,
            ),
        ]
    )
    inputs = []
    for slot, filename, urls, minimum, limit in files:
        target = root / filename
        if target.is_file() and not target.is_symlink():
            if not minimum <= target.stat().st_size <= limit:
                raise ExecutionError("engine_unavailable", "Built-in model has an invalid size.")
        else:
            if os.environ.get("TAILCAM_WORKER_OFFLINE") == "1":
                raise ExecutionError("engine_unavailable", "Worker model provisioning is offline.")
            if limit > remaining_bytes(root, maximum):
                raise ExecutionError("workspace_full", "Built-in model exceeds the scratch budget.")
            pending = root / (filename + ".download")
            for url in urls:
                written = 0
                try:
                    with httpx.Client(timeout=10, follow_redirects=True, trust_env=False) as client:
                        with client.stream("GET", url) as response, pending.open("xb") as stream:
                            response.raise_for_status()
                            for chunk in response.iter_bytes(65536):
                                if written + len(chunk) > limit:
                                    raise ValueError("built-in download exceeds its byte bound")
                                stream.write(chunk)
                                written += len(chunk)
                    if written < minimum:
                        raise ValueError("built-in download was truncated")
                    pending.replace(target)
                    break
                except Exception:
                    pending.unlink(missing_ok=True)
            if not target.is_file():
                raise ExecutionError("engine_unavailable", "Built-in model provisioning failed.")
        inputs.append({"slot": slot, "path": filename})
    return inputs


class TrainingParameters(Parameters):
    backend: Literal["yolo", "florence2", "qwen2.5-vl"] = "yolo"
    task: Literal["classification", "detection"] = "classification"
    epochs: int = Field(default=5, ge=1, le=1000)
    image_size: int = Field(default=224, ge=32, le=4096)
    device: Literal["cpu", "cuda", "mps", "auto"] = "auto"
    classes: list[str] = Field(default_factory=list, max_length=1000)
    model_format: Literal["weights", "directory-zip"] = "weights"
    seed: int = Field(default=1234, ge=0, le=2**31 - 1)


def safe_path(root: Path, relative: str) -> Path:
    from tailcam.jobs.models import PreparedOutput

    PreparedOutput(slot="path", path=relative, size_bytes=0, sha256="0" * 64)
    target = root.joinpath(*relative.split("/"))
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ExecutionError("invalid_request", "Symbolic links are not worker inputs.")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ExecutionError("invalid_request", "Path escaped its workspace.")
    return target


def extract_archive(source: Path, output: Path, max_bytes: int) -> Path:
    """Validate an entire portable regular-file ZIP manifest before extracting."""
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > 10000:
            raise ValueError("invalid archive entry count")
        names: set[str] = set()
        total = 0
        reserved = {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10)),
        }
        for entry in entries:
            name = entry.filename
            parts = name.split("/")
            key = unicodedata.normalize("NFC", name).casefold()
            if (
                len(name.encode()) > 1024
                or name.startswith("/")
                or "\\" in name
                or ":" in name
                or any(p in {"", ".", ".."} for p in parts)
                or any(ord(c) < 32 or c in '<>"|?*' for c in name)
                or any(
                    p.endswith((".", " ")) or p.split(".")[0].casefold() in reserved for p in parts
                )
                or key in names
                or entry.flag_bits & 1
                or entry.is_dir()
                or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG}
            ):
                raise ValueError("unsafe archive member")
            names.add(key)
            total += entry.file_size
        if total > max_bytes or any(
            str(parent) in names
            for name in names
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        ):
            raise ValueError("archive exceeds budget or has conflicting paths")
        output.mkdir()
        for entry in entries:
            target = output.joinpath(*entry.filename.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(entry) as inp, target.open("xb") as out:
                while data := inp.read(1024 * 1024):
                    written += len(data)
                    if written > entry.file_size:
                        raise ValueError("archive member changed size")
                    out.write(data)
            if written != entry.file_size:
                raise ValueError("truncated archive member")
    return output


def _archive_tree(source: Path, output: Path, max_bytes: int) -> Path:
    entries = sorted(source.rglob("*"))
    if len(entries) > 10000:
        raise ValueError("model contains too many files")
    files = []
    total = 0
    for path in entries:
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("model contains an unsafe file")
        if path.is_file():
            total += path.stat().st_size
            files.append(path)
    if total > max_bytes:
        raise ValueError("model exceeds output budget")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED) as archive:
        for path in files:
            archive.write(path, path.relative_to(source).as_posix())
    return output


def _load_image(path: Path):
    import cv2
    import numpy as np

    from tailcam.streaming.image_validation import raster_dimensions

    if path.stat().st_size > 12 * 1024**2:
        raise ValueError("image exceeds its byte limit")
    data = path.read_bytes()
    raster_dimensions(data)
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size > 4096 * 4096 * 3:
        raise ValueError("invalid or oversized image")
    return image


def _encode(
    task: str, params: dict, inputs: list[dict], root: Path, runtime: dict, budget: int
) -> tuple:
    from tailcam.timelapse.ffmpeg import (
        build_encode_command,
        build_smooth_command,
        ffmpeg_path,
        run_ffmpeg,
    )
    from tailcam.timelapse.service import _encode_frames

    options = EncodeParameters.model_validate(params)
    frames = root / "frames"
    frames.mkdir()
    images = [item for item in inputs if item["slot"].startswith("frame")]
    if not images:
        raise ExecutionError("input_unavailable", "No frame artifacts were provided.")
    for index, item in enumerate(images):
        source = safe_path(root, item["path"])
        if source.stat().st_size > remaining_bytes(root, budget):
            raise ExecutionError("workspace_full", "Frame copies exceed the scratch budget.")
        shutil.copyfile(source, frames / f"{index:06d}.jpg")
    if task == "timelapse_encode":
        result = _encode_frames(frames, options.fps)
        if result is None:
            raise ExecutionError("engine_unavailable", "No encoder produced a video.")
        video, thumb, (width, height, count) = result
        outputs = {"video": video}
        if thumb is not None:
            outputs["thumbnail"] = thumb
        return {"width": width, "height": height, "frames": count}, outputs
    exe = ffmpeg_path()
    if exe is None:
        raise ExecutionError("engine_unavailable", "FFmpeg is unavailable.")
    output = root / "smooth.mp4"
    if options.engine == "rife":
        from tailcam.timelapse.rife import build_rife_command, rife_path, run_rife

        rife = rife_path(runtime.get("rife_path", ""))
        if rife is None:
            raise ExecutionError("engine_unavailable", "RIFE is unavailable.")
        intermediate = root / "interpolated"
        intermediate.mkdir()
        multiplier = max(2, round(options.target_fps / options.fps))
        if not run_rife(
            build_rife_command(
                rife,
                frames,
                intermediate,
                len(images) * multiplier,
                runtime.get("rife_model", "rife-v4.6"),
            ),
            cwd=Path(rife).parent,
        ):
            raise ExecutionError("worker_failed", "RIFE interpolation failed.")
        command = build_encode_command(
            exe,
            str(intermediate / "*.png"),
            options.fps * multiplier,
            output,
            options.deflicker,
            options.quality,
        )
    else:
        command = build_smooth_command(
            exe,
            frames,
            options.fps,
            output,
            options.target_fps,
            options.interpolate,
            options.deflicker,
            options.quality,
        )
    if not run_ffmpeg(command) or not output.is_file():
        raise ExecutionError("worker_failed", "Interpolation did not produce an output.")
    return {"engine": options.engine, "target_fps": options.target_fps}, {"smooth": output}


def _model(inputs: list[dict], root: Path, archive: bool, budget: int) -> Path:
    item = next((item for item in inputs if item["slot"] == "model"), None)
    if item is None:
        raise ExecutionError("input_unavailable", "A preprovisioned model artifact is required.")
    path = safe_path(root, item["path"])
    if archive:
        return extract_archive(path, root / "model", remaining_bytes(root, budget))
    if path.suffix.lower() != ".pt":
        output = root / "base.pt"
        if path.stat().st_size > remaining_bytes(root, budget):
            raise ExecutionError("workspace_full", "Model copy exceeds the scratch budget.")
        shutil.copyfile(path, output)
        return output
    return path


def _inference(
    task: str,
    params: dict,
    inputs: list[dict],
    root: Path,
    runtime: dict,
    budget: int,
    persistent: bool = False,
) -> tuple:
    from tailcam.config import AIConfig

    options = InferenceParameters.model_validate(params)
    if (task == "printer_analysis" or options.backend == "ollama") and (
        runtime.get("ai", {}).get("provider", "ollama") != "ollama"
    ):
        raise ExecutionError(
            "unsupported_provider", "Isolated workers require an explicit supported provider."
        )
    if options.builtin:
        expected = "opencv" if options.builtin == "yolov4-tiny" else "yolo"
        if options.backend != expected or any(item["slot"] == "model" for item in inputs):
            raise ExecutionError("invalid_request", "Built-in model selection is inconsistent.")
        inputs = [*inputs, *_provision_builtin(options.builtin, root, budget)]
    images = [item for item in inputs if item["slot"] not in {"model", "model_config"}]
    if not images:
        raise ExecutionError("input_unavailable", "An image artifact is required.")
    key = json.dumps([task, params, runtime], sort_keys=True)
    analyzer: Any = None
    detector: Any = None
    if persistent and key in _ENGINE_CACHE:
        analyzer, detector = _ENGINE_CACHE[key]
    elif task == "printer_analysis":
        from tailcam.timelapse.analyzer import PrinterAnalyzer

        analyzer = PrinterAnalyzer(AIConfig(**runtime.get("ai", {})))
    elif options.backend == "ollama":
        from tailcam.ai.analyzer import OllamaAnalyzer

        analyzer = OllamaAnalyzer(AIConfig(**runtime.get("ai", {})))
    elif options.backend == "opencv":
        import cv2

        from tailcam.ai.detector import BuiltinDetector
        from tailcam.config import DetectionConfig

        model_input = next(item for item in inputs if item["slot"] == "model")
        cfg = next(item for item in inputs if item["slot"] == "model_config")
        net = cv2.dnn.readNetFromDarknet(
            str(safe_path(root, cfg["path"])), str(safe_path(root, model_input["path"]))
        )
        native = cv2.dnn.DetectionModel(net)
        native.setInputParams(size=(416, 416), scale=1.0 / 255.0, swapRB=True)
        detector = BuiltinDetector(DetectionConfig(confidence=options.confidence))
        detector._net_model = native
        # Use the already-created local DNN; never invoke automatic provisioning.
        detector.detect = detector._detect_opencv
    else:
        from tailcam.training.inference import LocalClassifier, LocalDetector, LocalVisionDetector

        model = _model(inputs, root, options.model_format == "directory-zip", budget)
        if options.backend == "classifier":
            analyzer = LocalClassifier(
                str(model), options.classes, device=runtime.get("device", "cpu")
            )
            if not analyzer.load():
                raise ExecutionError("engine_unavailable", "Classifier could not load.")
        else:
            detector = (
                LocalVisionDetector(
                    str(model), options.backend, managed=True, device=runtime.get("device", "cpu")
                )
                if options.backend in {"florence2", "qwen2.5-vl"}
                else LocalDetector(
                    str(model), options.confidence, device=runtime.get("device", "cpu")
                )
            )
            if not detector.load():
                raise ExecutionError("engine_unavailable", "Detector could not load.")
    if persistent:
        _ENGINE_CACHE[key] = (analyzer, detector)
    predictions = []
    try:
        for item in images:
            image = _load_image(safe_path(root, item["path"]))
            if analyzer is not None:
                value = analyzer.analyze(image)
                if value is None:
                    raise ExecutionError("inference_unavailable", "Analysis is unavailable.")
                predictions.append({"slot": item["slot"], **asdict(value)})
            else:
                assert detector is not None
                detections = detector.detect(image)
                if detections is None:
                    raise ExecutionError("inference_unavailable", "Inference is unavailable.")
                boxes = [asdict(box) for box in detections]
                if len(boxes) > 1000 or any(
                    not math.isfinite(float(box[key])) or not 0 <= float(box[key]) <= 1
                    for box in boxes
                    for key in ("confidence", "cx", "cy", "w", "h")
                ):
                    raise ExecutionError("inference_unavailable", "Inference result is invalid.")
                predictions.append({"slot": item["slot"], "boxes": boxes})
    finally:
        if not persistent and analyzer is not None and hasattr(analyzer, "close"):
            analyzer.close()
    return {"available": True, "outcome": "succeeded", "predictions": predictions}, {}


def _train(
    params: dict, inputs: list[dict], root: Path, budget: int, progress: Callable[[dict], None]
) -> tuple:
    from tailcam.training.labels import validate_class_label

    options = TrainingParameters.model_validate(params)
    for label in options.classes:
        validate_class_label(label)
    dataset = next((item for item in inputs if item["slot"] == "dataset"), None)
    if dataset is None:
        raise ExecutionError("input_unavailable", "A frozen dataset artifact is required.")
    data_dir = extract_archive(
        safe_path(root, dataset["path"]), root / "dataset", remaining_bytes(root, budget)
    )
    model = _model(inputs, root, options.model_format == "directory-zip", budget)
    if options.backend == "yolo":
        from tailcam.training import runner
        from tailcam.training.engine import torch_device

        if options.task == "detection":
            names = "\n".join(
                f"  {i}: {json.dumps(name)}" for i, name in enumerate(options.classes)
            )
            (data_dir / "data.yaml").write_text(
                f"path: {json.dumps(str(data_dir))}\ntrain: images/train\nval: images/val\nnames:\n"
                f"{names}\n",
                encoding="utf-8",
            )
        device = torch_device() if options.device == "auto" else options.device
        result = runner.train_model(
            str(model),
            data_dir,
            options.epochs,
            options.image_size,
            device,
            root,
            on_epoch=lambda epoch: progress({"epoch": epoch, "message": "Training"}),
            task=options.task,
            offline=True,
            seed=options.seed,
        )
        return {
            "metrics": result.get("metrics", {}),
            "classes": options.classes,
            "epochs": options.epochs,
            "backend": "yolo",
            "device": device,
        }, {
            "model": Path(result["model_path"]),
        }
    # These engines run only with fully materialized local model/dataset inputs.
    manifest = json.loads((data_dir / "samples.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or len(manifest) > 10000:
        raise ValueError("invalid training samples")
    samples = [(str(safe_path(data_dir, item["image"])), item["text"]) for item in manifest]
    if options.backend == "florence2":
        from tailcam.activelearning.florence import finetune_florence

        result = finetune_florence(
            samples,
            root / "trained",
            epochs=options.epochs,
            model_name=str(model),
            on_epoch=lambda epoch: progress({"epoch": epoch, "message": "Training"}),
            local_files_only=True,
            cache_dir=str(root / "cache"),
            device_override=options.device,
            seed=options.seed,
        )
    else:
        from tailcam.activelearning.qwen import finetune_qwen

        result = finetune_qwen(
            samples,
            root / "trained",
            epochs=options.epochs,
            model_name=str(model),
            on_epoch=lambda epoch: progress({"epoch": epoch, "message": "Training"}),
            local_files_only=True,
            cache_dir=str(root / "cache"),
            seed=options.seed,
        )
    output = _archive_tree(Path(result["model_path"]), root / "model.zip", budget)
    return {
        "metrics": result.get("metrics", {}),
        "classes": options.classes,
        "epochs": options.epochs,
        "backend": options.backend,
        "format": "directory-zip",
    }, {
        "model": output,
    }


def run_handler(request: dict, root: Path, progress: Callable[[dict], None]) -> dict:
    task = request["task"]
    parameters, inputs = dict(request.get("parameters", {})), request.get("inputs", [])
    budget = int(request["workspace_bytes"])
    runtime = dict(request.get("runtime", {}))
    device = "cpu"
    if request.get("gpu_slots", 0) and (
        task in {"timelapse_encode", "timelapse_interpolate", "printer_analysis"}
        or parameters.get("backend") in {"opencv", "ollama"}
    ):
        raise ExecutionError("engine_unavailable", "This engine does not use an admitted GPU slot.")
    if request.get("gpu_slots", 0):
        from tailcam.training.engine import torch_device

        device = torch_device()
    if request.get("gpu_slots", 0) and (
        device not in {"cuda", "mps"} or parameters.get("device") == "cpu"
    ):
        raise ExecutionError(
            "engine_unavailable", "An accelerator was required but is unavailable."
        )
    if task == "training":
        if parameters.get("device") in {"cuda", "mps"} and parameters["device"] != device:
            raise ExecutionError("engine_unavailable", "Requested training device is unavailable.")
        if parameters.get("backend") == "qwen2.5-vl" and device != "cuda":
            raise ExecutionError(
                "engine_unavailable", "Qwen training requires an admitted CUDA slot."
            )
        parameters["device"] = device
    runtime["device"] = device
    if task in {"timelapse_encode", "timelapse_interpolate"}:
        result, output_paths = _encode(task, parameters, inputs, root, runtime, budget)
    elif task in {"live_detection", "motion_description", "printer_analysis", "labeling"}:
        result, output_paths = _inference(
            task,
            parameters,
            inputs,
            root,
            runtime,
            budget,
            bool(request.get("persistent")),
        )
    elif task == "training":
        result, output_paths = _train(parameters, inputs, root, budget, progress)
    else:
        raise ExecutionError("unsupported_task", "No executor implements this task.")
    result.setdefault("device", device)
    from tailcam.jobs.models import PreparedOutput, ResultManifest

    outputs = []
    requested = set(request.get("output_slots", output_paths))
    total = 0
    for slot, path in output_paths.items():
        if slot not in requested:
            continue
        relative = path.relative_to(root).as_posix()
        path = safe_path(root, relative)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while data := stream.read(1024 * 1024):
                digest.update(data)
        size = path.stat().st_size
        total += size
        if total > request["output_bytes"]:
            raise ExecutionError("worker_failed", "Outputs exceed the admitted size.")
        outputs.append(
            PreparedOutput(slot=slot, path=relative, size_bytes=size, sha256=digest.hexdigest())
        )
    return ResultManifest(result=result, outputs=outputs).model_dump(mode="json")
