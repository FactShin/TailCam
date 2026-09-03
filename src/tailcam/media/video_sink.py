"""Video file writers for recordings and timelapse encodes.

Browsers only play H.264 (``avc1``) inside an ``.mp4``; OpenCV's default
``mp4v`` (MPEG-4 part 2) produces a file the Gallery can't play in Chrome or
Firefox — which reads as "recording didn't save". So the preferred sink pipes
raw BGR frames into ffmpeg (bundled via imageio-ffmpeg, or the system binary)
encoding libx264. When ffmpeg is unavailable or fails to start, we fall back to
``cv2.VideoWriter`` trying ``avc1`` first and ``mp4v`` last, so a clip is always
produced.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from tailcam import hostinfo
from tailcam.logging_setup import get_logger
from tailcam.proc import NO_WINDOW

log = get_logger(__name__)


class VideoSink(Protocol):
    codec: str

    def write(self, image: np.ndarray) -> bool: ...

    def close(self) -> bool: ...


def _even(size: tuple[int, int]) -> tuple[int, int]:
    """yuv420p needs even dimensions; round down so ffmpeg never rejects a frame."""
    w, h = size
    return max(2, w - (w % 2)), max(2, h - (h % 2))


class FfmpegPipeSink:
    """Stream BGR frames to an ffmpeg child process writing H.264/mp4."""

    codec = "h264"

    def __init__(self, ffmpeg: str, path: Path, fps: float, size: tuple[int, int]) -> None:
        self.path = path
        self.size = size  # incoming frame size
        self._out_size = _even(size)
        w, h = size
        ow, oh = self._out_size
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
            "-r", f"{max(1.0, float(fps)):.3f}", "-i", "pipe:0",
            "-an", "-c:v", "libx264", "-preset", hostinfo.x264_preset(),
            "-crf", "23", "-pix_fmt", "yuv420p",
            "-threads", str(hostinfo.encode_threads()),
            "-vf", f"scale={ow}:{oh}",
            # No +faststart for live recordings: it rewrites the whole file
            # after the trailer, which on a Pi SD card takes longer than any
            # sane close timeout for a long clip — the encoder got killed
            # mid-rewrite and the clip was lost. Streaming playback of an mp4
            # with the moov at the end still works over HTTP range requests.
            str(path),
        ]
        kwargs: dict[str, Any] = {}
        if NO_WINDOW:
            kwargs["creationflags"] = NO_WINDOW
        self._proc = subprocess.Popen(  # noqa: S603 - fixed command
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, **kwargs,
        )
        self._failed = False
        self.frames = 0

    @property
    def failed(self) -> bool:
        """The encoder died (disk full, killed) — the session should end."""
        return self._failed or self._proc.poll() is not None

    def write(self, image: np.ndarray) -> bool:
        if self._failed or self._proc.stdin is None:
            return False
        h, w = image.shape[:2]
        if (w, h) != self.size:
            import cv2

            image = cv2.resize(image, self.size)
        try:
            self._proc.stdin.write(np.ascontiguousarray(image).tobytes())
            self.frames += 1
            return True
        except (BrokenPipeError, OSError) as exc:
            self._failed = True
            log.error("ffmpeg recording pipe broke for %s: %s", self.path.name, exc)
            return False

    def close(self) -> bool:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except OSError:
            pass
        err = b""
        try:
            # communicate() drains stderr while waiting so a chatty encoder
            # can't deadlock on a full pipe.
            _, err = self._proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            try:
                _, err = self._proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                pass
            log.error("ffmpeg did not finish %s in time; killed", self.path.name)
            self._discard_partial()
            return False
        if self._proc.returncode != 0:
            log.error(
                "ffmpeg exited %s for %s: %s",
                self._proc.returncode, self.path.name,
                (err or b"").decode("utf-8", "ignore")[-400:],
            )
            self._discard_partial()
            return False
        ok = not self._failed and self.frames > 0
        if not ok:
            self._discard_partial()
        return ok

    def _discard_partial(self) -> None:
        """A clip the encoder didn't finish has no moov atom and can't play;
        leaving it on disk would only hide space from retention."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


class OpenCVSink:
    """cv2.VideoWriter fallback; tries avc1 (playable) before mp4v."""

    def __init__(self, path: Path, fps: float, size: tuple[int, int]) -> None:
        import cv2

        self.path = path
        self.size = size
        self.codec = ""
        self._writer: Any = None
        self.frames = 0
        for fourcc in ("avc1", "mp4v"):
            writer = cv2.VideoWriter(
                str(path), cv2.VideoWriter_fourcc(*fourcc), float(max(1.0, fps)), size  # type: ignore[attr-defined]
            )
            if writer.isOpened():
                self._writer = writer
                self.codec = fourcc
                break
            writer.release()

    @property
    def opened(self) -> bool:
        return self._writer is not None

    @property
    def failed(self) -> bool:
        return False

    def write(self, image: np.ndarray) -> bool:
        if self._writer is None:
            return False
        h, w = image.shape[:2]
        if (w, h) != self.size:
            import cv2

            image = cv2.resize(image, self.size)
        self._writer.write(image)
        self.frames += 1
        return True

    def close(self) -> bool:
        if self._writer is None:
            return False
        self._writer.release()
        self._writer = None
        return self.frames > 0


# Refuse to start a recording with less than this much free space: a clip
# that dies on ENOSPC is worth nothing, and the disk is better left to the
# retention pruner.
MIN_FREE_BYTES = 200 * 1024 * 1024


def free_bytes(path: Path) -> int | None:
    import shutil

    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def open_video_sink(path: Path, fps: float, size: tuple[int, int]) -> VideoSink | None:
    """Best available mp4 writer for ``size`` frames at ``fps``; None if none opens."""
    from tailcam.timelapse.ffmpeg import ffmpeg_path

    path.parent.mkdir(parents=True, exist_ok=True)
    free = free_bytes(path)
    if free is not None and free < MIN_FREE_BYTES:
        log.error(
            "not recording %s: only %.0f MB free on %s", path.name, free / 1e6, path.parent
        )
        return None
    exe = ffmpeg_path()
    if exe:
        try:
            sink = FfmpegPipeSink(exe, path, fps, size)
            # A binary that dies immediately (bad build, missing libx264)
            # surfaces here rather than on the first write.
            if sink._proc.poll() is None:
                return sink
            sink.close()
            log.warning("ffmpeg sink exited immediately; falling back to OpenCV writer")
        except (OSError, ValueError) as exc:
            log.warning("ffmpeg sink unavailable (%s); falling back to OpenCV writer", exc)
    cv_sink = OpenCVSink(path, fps, size)
    if cv_sink.opened:
        if cv_sink.codec == "mp4v":
            log.warning(
                "recording %s uses mp4v (no H.264 encoder available) — it may not play "
                "in every browser; install ffmpeg for H.264",
                path.name,
            )
        return cv_sink
    log.error("no video writer could open %s", path)
    return None
