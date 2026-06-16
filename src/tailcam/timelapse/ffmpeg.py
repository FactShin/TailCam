"""Locate an ffmpeg binary and build the post-processing command.

ffmpeg turns the timelapse's raw frames into smooth, flowing motion via motion-
compensated frame interpolation (``minterpolate``) and optional deflicker. We
prefer a system ffmpeg (fuller build, faster) and otherwise fall back to the
static binary bundled by ``imageio-ffmpeg`` so smoothing works with no setup.
"""

from __future__ import annotations

import importlib.util
import os
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
_QUALITY_ARGS = {
    "standard": ("medium", "20"),
    "high": ("slow", "18"),
    "maximum": ("slower", "15"),
}


def output_quality_args(quality: str) -> tuple[str, str]:
    """Return a fixed FFmpeg preset/CRF pair; never accept arbitrary arguments."""
    try:
        return _QUALITY_ARGS[quality]
    except KeyError as exc:
        raise ValueError("quality must be standard, high, or maximum") from exc


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
    # Platform probes can be overridden by packaging/tests. Discovering the
    # installed wheel without importing it still finds this machine's binary.
    spec = importlib.util.find_spec("imageio_ffmpeg")
    if spec and spec.origin:
        binaries = Path(spec.origin).parent / "binaries"
        bundled = next((path for path in binaries.glob("ffmpeg-*") if path.is_file()), None)
        if bundled is not None:
            # get_ffmpeg_exe() normally sets the exec bit; this fallback path
            # didn't go through it, so ensure the binary is runnable.
            try:
                os.chmod(bundled, bundled.stat().st_mode | 0o111)
            except OSError:  # pragma: no cover
                pass
            return str(bundled)
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
    quality: str = "high",
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
    preset, crf = output_quality_args(quality)
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(max(1, src_fps)),
        "-i", str(frames_dir / "%06d.jpg"),
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ]


def build_encode_command(
    ffmpeg: str,
    input_glob: str,
    fps: int,
    out_path: Path,
    deflicker: bool,
    quality: str = "high",
) -> list[str]:
    """Encode an already-prepared frame sequence (e.g. RIFE output) to mp4 at
    ``fps``. No interpolation here — the frames are the final cadence."""
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(max(1, fps)),
        "-pattern_type", "glob", "-i", input_glob,
    ]
    if deflicker:
        cmd += ["-vf", "deflicker=mode=pm:size=10"]
    preset, crf = output_quality_args(quality)
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ]
    return cmd


def run_ffmpeg(cmd: list[str], timeout: float = 1800.0) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            log.error("ffmpeg failed (%d): %s", proc.returncode, proc.stderr[-800:])
        return proc.returncode == 0
    except Exception as exc:  # pragma: no cover - timeout / OS error
        log.error("ffmpeg error: %s", exc)
        return False
