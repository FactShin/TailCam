"""Human-in-the-loop active learning.

A labeling model watches frames from your cameras (or an existing dataset),
keeps confident detections as machine labels, and sends only the uncertain
frames to Label Studio for human review. Reviewed annotations sync back into
the training dataset, which then fine-tunes the model of your choice — YOLO
(the existing Ultralytics pipeline), Florence-2, or Qwen2.5-VL via Unsloth.

Module map:

- :mod:`annotations` — the canonical TailCam annotation format and converters
  to/from Label Studio and per-model training formats.
- :mod:`backends` — the labeling-model abstraction (built-in YOLO, trained/BYO
  models, Ollama, Florence-2, Qwen2.5-VL) with availability reporting.
- :mod:`florence` / :mod:`qwen` — the VLM backends (lazy heavy imports,
  graceful degradation when torch/CUDA are missing).
- :mod:`labelstudio` — Label Studio integration via the official Python SDK.
- :mod:`service` — the ActiveLearningService loop: capture → infer → route →
  review → sync → fine-tune.
"""
