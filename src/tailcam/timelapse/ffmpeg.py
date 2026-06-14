"""Locate an ffmpeg binary and build the post-processing command.

ffmpeg turns the timelapse's raw frames into smooth, flowing motion via motion-
compensated frame interpolation (``minterpolate``) and optional deflicker. We
prefer a system ffmpeg (fuller build, faster) and otherwise fall back to the
static binary bundled by ``imageio-ffmpeg`` so smoothing works with no setup.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_KNOWN_BINARIES = {
    "darwin": ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"],
    "win32": [r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"],
}


def ffmpeg_path() -> str | None:
    """Resolve an ffmpeg executable: system PATH, known locations, then bundled."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _KNOWN_BINARIES.get(sys.platform, []):
        if Path(candidate).exists():
            return candidate
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # pragma: no cover - imageio_ffmpeg should be installed
        pass
    return None


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def ffmpeg_source() -> str:
    """"system" | "bundled" | "missing" — for the dashboard status panel."""
    path = ffmpeg_path()
    if not path:
        return "missing"
    return "bundled" if "imageio_ffmpeg" in path else "system"


def ffmpeg_version() -> str | None:
    path = ffmpeg_path()
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=5
        ).stdout
        first = out.splitlines()[0] if out else ""
        parts = first.split()  # "ffmpeg version 7.0.2-static ..." -> parts[2]
        return parts[2] if len(parts) >= 3 else None
    except Exception:  # pragma: no cover
        return None


def build_smooth_command(
    ffmpeg: str,
    frames_dir: Path,
    src_fps: int,
    out_path: Path,
    target_fps: int,
    interpolate: bool,
    deflicker: bool,
) -> list[str]:
    """ffmpeg invocation that reads the numbered source frames and writes a
    smoothed H.264 mp4. ``minterpolate`` synthesizes in-between frames up to
    ``target_fps``; ``deflicker`` evens out auto-exposure shimmer."""
    filters: list[str] = []
    if deflicker:
        filters.append("deflicker=mode=pm:size=10")
    if interpolate:
        filters.append(
            f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
        )
    else:
        filters.append(f"fps={target_fps}")
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(max(1, src_fps)),
        "-i", str(frames_dir / "%06d.jpg"),
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ]


def run_ffmpeg(cmd: list[str], timeout: float = 1800.0) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            log.error("ffmpeg failed (%d): %s", proc.returncode, proc.stderr[-800:])
        return proc.returncode == 0
    except Exception as exc:  # pragma: no cover - timeout / OS error
        log.error("ffmpeg error: %s", exc)
        return False
