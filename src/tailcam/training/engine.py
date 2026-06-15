"""Detect the local training engine (Ultralytics + torch) and its GPU device.

We never bundle torch — it's huge and platform/GPU-specific — so this reports
whether the optional ``tailcam[training]`` extra is installed and which device
training would use (CUDA on the Windows box, MPS on the Mac, else CPU).
"""

from __future__ import annotations

from tailcam.logging_setup import get_logger

log = get_logger(__name__)


def torch_device() -> str:
    """Best available device: 'cuda' | 'mps' | 'cpu' (or 'none' if no torch)."""
    try:
        import torch
    except Exception:
        return "none"
    try:
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:  # pragma: no cover - defensive
        pass
    return "cpu"


def engine_available() -> bool:
    try:
        import torch  # noqa: F401
        import ultralytics  # noqa: F401

        return True
    except Exception:
        return False


def engine_version() -> str | None:
    try:
        import ultralytics

        return getattr(ultralytics, "__version__", None)
    except Exception:
        return None


def engine_info() -> dict:
    available = engine_available()
    return {
        "available": available,
        "framework": "ultralytics",
        "version": engine_version() if available else None,
        "device": torch_device() if available else "none",
    }
