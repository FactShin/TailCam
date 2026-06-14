"""Optional RIFE interpolation engine (rife-ncnn-vulkan).

RIFE (Real-time Intermediate Flow Estimation) produces higher-quality in-between
frames than ffmpeg's ``minterpolate``, using the GPU. We don't bundle it — there
is no reliable cross-platform wheel — so this detects an installed
``rife-ncnn-vulkan`` binary (PATH, env, config, or known locations) and shells
out to it, mirroring the Ollama integration. A failed run falls back to ffmpeg.

The binary resolves its model folder relative to its own directory, so the
runner sets ``cwd`` to the binary's location and passes ``-m <model_name>``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_BINARY_NAMES = ["rife-ncnn-vulkan", "rife-ncnn-vulkan.exe", "rife"]
_KNOWN_DIRS = {
    "darwin": ["/opt/homebrew/bin", "/usr/local/bin"],
    "win32": [r"C:\Program Files\rife-ncnn-vulkan", r"C:\rife-ncnn-vulkan"],
}


def rife_path(configured: str = "") -> str | None:
    """Resolve a rife-ncnn-vulkan binary: explicit config/env, PATH, then known
    locations. Returns None if RIFE isn't installed."""
    for explicit in (configured, os.environ.get("TAILCAM_RIFE", "")):
        if explicit and Path(explicit).exists():
            return explicit
    for name in _BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found
    dirs = [Path.home() / ".local" / "bin", *map(Path, _KNOWN_DIRS.get(sys.platform, []))]
    for directory in dirs:
        for name in _BINARY_NAMES:
            candidate = directory / name
            if candidate.exists():
                return str(candidate)
    return None


def rife_available(configured: str = "") -> bool:
    return rife_path(configured) is not None


def build_rife_command(
    rife: str, in_dir: Path, out_dir: Path, target_frames: int, model: str = ""
) -> list[str]:
    """rife-ncnn-vulkan: read frames from ``in_dir``, write ``target_frames``
    uniformly-interpolated frames to ``out_dir``."""
    cmd = [rife, "-i", str(in_dir), "-o", str(out_dir), "-n", str(max(1, target_frames))]
    if model:
        cmd += ["-m", model]
    return cmd


def run_rife(cmd: list[str], cwd: Path | None = None, timeout: float = 3600.0) -> bool:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None
        )
        if proc.returncode != 0:
            log.error("rife failed (%d): %s", proc.returncode, (proc.stderr or "")[-800:])
        return proc.returncode == 0
    except Exception as exc:  # pragma: no cover - timeout / OS error
        log.error("rife error: %s", exc)
        return False
