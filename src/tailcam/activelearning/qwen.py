"""Qwen2.5-VL labeling backend, with fine-tuning via Unsloth.

Inference uses plain transformers (works on CUDA, Apple MPS, or CPU — slow but
functional), asking the model for grounded detections as JSON. Fine-tuning
uses Unsloth's 4-bit QLoRA path, which requires an NVIDIA CUDA GPU — that
means Linux (or WSL); on macOS the backend reports inference-only support
instead of failing, per TailCam's cross-platform rules.

Requirements: ``pip install 'tailcam[qwen-vl]'`` for inference; additionally
``pip install unsloth`` on a CUDA machine for fine-tuning.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np

from tailcam.activelearning.backends import BackendInfo
from tailcam.ai.analyzer import Detection
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"
UNSLOTH_MODEL = "unsloth/Qwen2.5-VL-3B-Instruct-bnb-4bit"

# Like Florence-2, Qwen doesn't return calibrated per-box scores; committed
# boxes get this confidence so threshold routing still works.
_QWEN_CONFIDENCE = 0.7

_DETECT_PROMPT = (
    "Detect every distinct object in this image. Respond ONLY with a JSON list; "
    'each item is {"bbox_2d": [x1, y1, x2, y2], "label": "<short label>"} '
    "using absolute pixel coordinates. Respond [] if there is nothing notable."
)


def _inference_packages_missing() -> list[str]:
    return [
        pkg for pkg in ("torch", "transformers", "qwen_vl_utils")
        if importlib.util.find_spec(pkg) is None
    ]


class QwenVLBackend:
    """Grounded detection with Qwen2.5-VL via transformers."""

    def __init__(self, model_name: str = DEFAULT_MODEL, model_path: str = "") -> None:
        # model_path: a fine-tuned checkpoint/adapter dir; falls back to the hub name.
        self.model_name = model_path or model_name
        self._model = None
        self._processor = None
        self._device = "cpu"
        self._load_error = ""

    def info(self) -> BackendInfo:
        missing = _inference_packages_missing()
        if missing:
            detail = (
                "install " + ", ".join(missing).replace("qwen_vl_utils", "qwen-vl-utils")
                + " — pip install 'tailcam[qwen-vl]'"
            )
            return BackendInfo(id="qwen2.5-vl", name="Qwen2.5-VL", kind="vlm",
                               available=False, detail=detail)
        detail = self._load_error or (
            "ready" if self._model is not None else "ready (loads on first frame)"
        )
        return BackendInfo(id="qwen2.5-vl", name="Qwen2.5-VL", kind="vlm",
                           available=not self._load_error, detail=detail)

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        try:
            import torch
            from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

            from tailcam.training.engine import torch_device

            device = torch_device()
            self._device = device if device in ("cuda", "mps") else "cpu"
            dtype = torch.float16 if self._device in ("cuda", "mps") else torch.float32
            self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                self.model_name, torch_dtype=dtype
            ).to(self._device)
            self._processor = AutoProcessor.from_pretrained(self.model_name)
            log.info("qwen2.5-vl loaded (%s on %s)", self.model_name, self._device)
            return True
        except Exception as exc:
            self._load_error = f"model failed to load: {exc}"
            log.warning("qwen2.5-vl load failed: %s", exc)
            return False

    def predict(self, image: np.ndarray) -> list[Detection] | None:
        if not self._load():
            return None
        try:
            import cv2
            from PIL import Image
            from qwen_vl_utils import process_vision_info

            pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": pil},
                        {"type": "text", "text": _DETECT_PROMPT},
                    ],
                }
            ]
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = self._processor(
                text=[text], images=image_inputs, videos=video_inputs,
                padding=True, return_tensors="pt",
            ).to(self._device)
            generated = self._model.generate(**inputs, max_new_tokens=512, do_sample=False)
            trimmed = [
                out[len(inp):] for inp, out in zip(inputs.input_ids, generated, strict=False)
            ]
            answer = self._processor.batch_decode(
                trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]
            return parse_qwen_detections(answer, pil.width, pil.height)
        except Exception as exc:
            log.warning("qwen2.5-vl inference failed: %s", exc)
            return None


def parse_qwen_detections(answer: str, width: int, height: int) -> list[Detection]:
    """Parse the model's JSON answer (possibly wrapped in markdown fences or
    prose) into normalized detections. Unparseable answers yield []."""
    match = re.search(r"\[.*\]", answer, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    detections: list[Detection] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        box = item.get("bbox_2d") or item.get("bbox") or []
        label = str(item.get("label", "")).strip()
        if not label or len(box) != 4:
            continue
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
                label=label,
                confidence=_QWEN_CONFIDENCE,
                cx=min(1.0, max(0.0, (x1 + x2) / 2 / max(1, width))),
                cy=min(1.0, max(0.0, (y1 + y2) / 2 / max(1, height))),
                w=min(1.0, w),
                h=min(1.0, h),
            )
        )
    return detections


# -- fine-tuning ----------------------------------------------------------------


def qwen_finetune_support() -> tuple[bool, str]:
    """(available, detail) for Unsloth QLoRA fine-tuning on this machine.

    Unsloth requires an NVIDIA CUDA GPU; macOS never qualifies, so it gets a
    clear inference-only message rather than a hard failure at train time.
    """
    if sys.platform == "darwin":
        return False, (
            "Unsloth needs an NVIDIA CUDA GPU — not available on macOS. "
            "Inference works here; fine-tune on a Linux CUDA machine."
        )
    if importlib.util.find_spec("unsloth") is None:
        return False, "install Unsloth on a CUDA machine: pip install unsloth"
    from tailcam.training.engine import torch_device

    if torch_device() != "cuda":
        return False, "Unsloth needs an NVIDIA CUDA GPU (none detected)"
    return True, "ready · CUDA GPU via Unsloth QLoRA"


def finetune_qwen(
    samples: list[tuple[str, str]],
    out_dir: Path,
    epochs: int = 1,
    model_name: str = UNSLOTH_MODEL,
    on_epoch=None,
    should_stop=None,
) -> dict:
    """QLoRA fine-tune Qwen2.5-VL with Unsloth on ``(image_path, json_target)``
    pairs (the target is the JSON detection list :func:`annotations.to_qwen_json`
    builds). Saves LoRA adapters + processor to ``out_dir``.

    Raises RuntimeError with an actionable message when unsupported (macOS, no
    CUDA, unsloth missing) — callers surface it in the UI/run log.
    """
    ok, detail = qwen_finetune_support()
    if not ok:
        raise RuntimeError(f"Qwen2.5-VL fine-tuning unavailable: {detail}")
    from PIL import Image
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastVisionModel  # noqa: PLC0415 - CUDA-only import
    from unsloth.trainer import UnslothVisionDataCollator

    model, tokenizer = FastVisionModel.from_pretrained(
        model_name, load_in_4bit=True, use_gradient_checkpointing="unsloth"
    )
    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers=False,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=16,
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        random_state=1234,
    )

    dataset = []
    for image_path, target in samples:
        try:
            pil = Image.open(image_path).convert("RGB")
        except OSError:
            continue
        dataset.append(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image": pil},
                            {"type": "text", "text": _DETECT_PROMPT},
                        ],
                    },
                    {"role": "assistant", "content": [{"type": "text", "text": target}]},
                ]
            }
        )
    if not dataset:
        raise RuntimeError("no readable training samples")

    FastVisionModel.for_training(model)
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        data_collator=UnslothVisionDataCollator(model, tokenizer),
        train_dataset=dataset,
        args=SFTConfig(
            per_device_train_batch_size=1,
            gradient_accumulation_steps=4,
            num_train_epochs=epochs,
            learning_rate=2e-4,
            logging_steps=1,
            optim="adamw_8bit",
            seed=1234,
            output_dir=str(out_dir / "checkpoints"),
            report_to="none",
            remove_unused_columns=False,
            dataset_text_field="",
            dataset_kwargs={"skip_prepare_dataset": True},
            max_seq_length=2048,
        ),
    )
    result = trainer.train()
    if on_epoch is not None:
        on_epoch(epochs)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))
    loss = getattr(result, "training_loss", None)
    metrics = {"loss": round(float(loss), 4)} if loss is not None else {}
    return {"model_path": str(out_dir), "metrics": metrics}
