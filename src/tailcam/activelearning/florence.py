"""Florence-2 labeling backend (Microsoft's open vision foundation model).

Florence-2 does zero-shot object detection (the ``<OD>`` task), which makes it
a strong pre-labeler for classes the built-in COCO detector doesn't know. All
heavy imports (torch, transformers) are lazy and every failure degrades to an
availability note instead of an exception, so TailCam runs fine without them.

Requirements: ``pip install 'tailcam[florence2]'`` (transformers, timm,
einops, torch). Inference runs on CUDA, Apple MPS, or CPU (slow). Fine-tuning
is implemented with a plain torch loop over the Florence OD string format —
practical on a CUDA GPU, possible-but-slow on MPS/CPU; see the docs page for
limitations.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import numpy as np

from tailcam.activelearning.backends import BackendInfo
from tailcam.ai.analyzer import Detection
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "microsoft/Florence-2-base"

# Florence-2 does not emit calibrated per-box confidences from the <OD> task;
# boxes it commits to are treated as this confidence so thresholding behaves.
_FLORENCE_CONFIDENCE = 0.75


def _packages_missing() -> list[str]:
    return [
        pkg for pkg in ("torch", "transformers", "timm", "einops")
        if importlib.util.find_spec(pkg) is None
    ]


class Florence2Backend:
    """Zero-shot object detection with Florence-2."""

    def __init__(self, model_name: str = DEFAULT_MODEL, model_path: str = "") -> None:
        # model_path: a fine-tuned checkpoint directory; falls back to the hub name.
        self.model_name = model_path or model_name
        # Lazily-loaded transformers handles (heavy optional deps).
        self._model: Any = None
        self._processor: Any = None
        self._device = "cpu"
        self._load_error = ""

    def info(self) -> BackendInfo:
        missing = _packages_missing()
        if missing:
            detail = (
                "install " + ", ".join(missing)
                + " — pip install 'tailcam[florence2]'"
            )
            return BackendInfo(id="florence2", name="Florence-2", kind="vlm",
                               available=False, detail=detail)
        detail = self._load_error or (
            "ready" if self._model is not None else "ready (loads on first frame)"
        )
        return BackendInfo(id="florence2", name="Florence-2", kind="vlm",
                           available=not self._load_error, detail=detail)

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoProcessor

            from tailcam.training.engine import torch_device

            device = torch_device()
            self._device = device if device in ("cuda", "mps") else "cpu"
            dtype = torch.float16 if self._device == "cuda" else torch.float32
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_name, torch_dtype=dtype, trust_remote_code=True
            ).to(self._device)
            self._processor = AutoProcessor.from_pretrained(
                self.model_name, trust_remote_code=True
            )
            log.info("florence-2 loaded (%s on %s)", self.model_name, self._device)
            return True
        except Exception as exc:
            self._load_error = f"model failed to load: {exc}"
            log.warning("florence-2 load failed: %s", exc)
            return False

    def predict(self, image: np.ndarray) -> list[Detection] | None:
        if not self._load():
            return None
        try:
            import cv2
            from PIL import Image

            pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            task = "<OD>"
            inputs = self._processor(text=task, images=pil, return_tensors="pt")
            inputs = {
                k: (v.to(self._device) if hasattr(v, "to") else v)
                for k, v in inputs.items()
            }
            if self._device == "cuda":
                inputs["pixel_values"] = inputs["pixel_values"].half()
            generated = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=512,
                num_beams=3,
                do_sample=False,
            )
            text = self._processor.batch_decode(generated, skip_special_tokens=False)[0]
            parsed = self._processor.post_process_generation(
                text, task=task, image_size=(pil.width, pil.height)
            )
            return _od_to_detections(parsed.get(task) or {}, pil.width, pil.height)
        except Exception as exc:
            log.warning("florence-2 inference failed: %s", exc)
            return None


def _od_to_detections(od: dict, width: int, height: int) -> list[Detection]:
    """Florence <OD> output ({'bboxes': [[x1,y1,x2,y2]…], 'labels': […]}) to
    normalized center/size detections."""
    detections: list[Detection] = []
    for box, label in zip(od.get("bboxes") or [], od.get("labels") or [], strict=False):
        try:
            x1, y1, x2, y2 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        w = max(0.0, x2 - x1) / max(1, width)
        h = max(0.0, y2 - y1) / max(1, height)
        if w <= 0 or h <= 0:
            continue
        detections.append(
            Detection(
                label=str(label),
                confidence=_FLORENCE_CONFIDENCE,
                cx=(x1 + x2) / 2 / max(1, width),
                cy=(y1 + y2) / 2 / max(1, height),
                w=w,
                h=h,
            )
        )
    return detections


# -- fine-tuning ----------------------------------------------------------------


def florence_finetune_support() -> tuple[bool, str]:
    """(available, human-readable detail) for fine-tuning on this machine."""
    missing = _packages_missing()
    if missing:
        return False, (
            "install " + ", ".join(missing) + " — pip install 'tailcam[florence2]'"
        )
    from tailcam.training.engine import torch_device

    device = torch_device()
    if device == "cuda":
        return True, "ready · CUDA GPU"
    if device == "mps":
        return True, "ready · Apple GPU (MPS) — slower than CUDA"
    return True, "CPU only — fine-tuning will be very slow"


def finetune_florence(
    samples: list[tuple[str, str]],
    out_dir: Path,
    epochs: int = 3,
    model_name: str = DEFAULT_MODEL,
    on_epoch=None,
    should_stop=None,
    lr: float = 1e-6,
) -> dict:
    """Fine-tune Florence-2 on ``(image_path, od_target_string)`` pairs.

    A deliberately simple full-precision AdamW loop (batch size 1) — robust
    across CUDA/MPS/CPU at the cost of speed. Saves the checkpoint + processor
    to ``out_dir`` and returns {'model_path', 'metrics'}. Raises RuntimeError
    with an actionable message when the environment can't train.
    """
    ok, detail = florence_finetune_support()
    if not ok:
        raise RuntimeError(f"Florence-2 fine-tuning unavailable: {detail}")
    import torch
    from PIL import Image
    from transformers import AutoModelForCausalLM, AutoProcessor

    from tailcam.training.engine import torch_device

    device = torch_device()
    device = device if device in ("cuda", "mps") else "cpu"
    model = AutoModelForCausalLM.from_pretrained(
        model_name, trust_remote_code=True
    ).to(device)
    processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    model.train()

    losses: list[float] = []
    for epoch in range(epochs):
        epoch_loss = 0.0
        n = 0
        for image_path, target in samples:
            if should_stop is not None and should_stop():
                break
            try:
                pil = Image.open(image_path).convert("RGB")
            except OSError:
                continue
            inputs = processor(text="<OD>", images=pil, return_tensors="pt").to(device)
            labels = processor.tokenizer(
                target, return_tensors="pt", max_length=512, truncation=True
            ).input_ids.to(device)
            out = model(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                labels=labels,
            )
            out.loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            epoch_loss += float(out.loss.detach())
            n += 1
        if n:
            losses.append(epoch_loss / n)
        if on_epoch is not None:
            on_epoch(epoch + 1)
        if should_stop is not None and should_stop():
            break

    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir))
    processor.save_pretrained(str(out_dir))
    metrics = {"loss": round(losses[-1], 4)} if losses else {}
    return {"model_path": str(out_dir), "metrics": metrics}
