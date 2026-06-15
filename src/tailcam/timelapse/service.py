"""Timelapse lifecycle: start capture, finalize (encode), list, delete.

Capture runs in a per-job thread (:class:`TimelapseCaptureWorker`). Encoding —
turning the stored JPEG frames into an mp4 — also runs off the request thread so
a long print's many frames don't block the API. Source frames are retained after
encoding so a future phase can re-stitch them with interpolation/deflicker.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import cv2

from tailcam import paths
from tailcam.camera.manager import CameraManager
from tailcam.config import TimelapseConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.models import TimelapseRecord
from tailcam.persistence.store import Store
from tailcam.streaming.encoder import encode_jpeg
from tailcam.timelapse.ffmpeg import (
    build_encode_command,
    build_smooth_command,
    ffmpeg_path,
    run_ffmpeg,
)
from tailcam.timelapse.rife import build_rife_command, rife_available, rife_path, run_rife
from tailcam.timelapse.worker import TimelapseCaptureWorker

log = get_logger(__name__)

_TERMINAL = {"complete", "error"}
_THUMB_WIDTH = 320


class TimelapseService:
    def __init__(self, manager: CameraManager, store: Store, config: TimelapseConfig) -> None:
        self._manager = manager
        self._store = store
        self._config = config
        self._workers: dict[int, TimelapseCaptureWorker] = {}
        self._encoding: set[int] = set()
        self._smoothing: set[int] = set()
        self._lock = threading.Lock()

    # -- start -------------------------------------------------------------
    def start(
        self,
        camera_id: str,
        name: str | None = None,
        interval_seconds: float | None = None,
        output_fps: int | None = None,
        duration_seconds: float = 0.0,
    ) -> TimelapseRecord | None:
        buffer = self._manager.get_buffer(camera_id)
        if buffer is None:
            return None
        interval = interval_seconds or self._config.default_interval_seconds
        fps = output_fps or self._config.default_output_fps
        cam = self._manager.get(camera_id)
        cam_name = cam.name if cam else camera_id
        ts = time.time()
        record = TimelapseRecord(
            id=None,
            camera_id=camera_id,
            name=name or f"{cam_name} timelapse",
            state="capturing",
            mode="interval",
            interval_seconds=interval,
            output_fps=fps,
            frames_captured=0,
            created_ts=ts,
            start_ts=ts,
            end_ts=None,
            frames_dir="",  # filled in once we know the id
        )
        tl_id = self._store.add_timelapse(record)
        record.id = tl_id
        frames_dir = paths.timelapse_dir() / str(tl_id) / "frames"
        record.frames_dir = str(frames_dir)
        self._store.update_timelapse(tl_id, frames_dir=str(frames_dir))

        worker = TimelapseCaptureWorker(
            tl_id, camera_id, buffer, frames_dir,
            interval_seconds=interval,
            jpeg_quality=self._config.jpeg_quality,
            max_frames=self._config.max_frames,
            duration_seconds=duration_seconds,
            on_frame=lambda n: self._on_frame(tl_id, n),
            on_complete=lambda: self._finalize_async(tl_id),
        )
        with self._lock:
            self._workers[tl_id] = worker
        worker.start()
        log.info("timelapse %s started on %s (interval=%.1fs)", tl_id, camera_id, interval)
        return record

    def _on_frame(self, tl_id: int, n: int) -> None:
        # Persist progress occasionally so a crash loses little; live reads use
        # the in-memory worker counter (see _patch_live).
        if n % 10 == 0:
            self._store.update_timelapse(tl_id, frames_captured=n)

    # -- stop / finalize ---------------------------------------------------
    def stop(self, tl_id: int) -> TimelapseRecord | None:
        """Stop a running capture and start encoding. Returns immediately — the
        worker join + encode happen on a background thread so the HTTP request
        (and the UI) never blocks waiting for the capture thread to wind down."""
        with self._lock:
            worker = self._workers.get(tl_id)
        if worker is None:
            # Nothing capturing (already stopped/interrupted): finalize any frames.
            self._finalize_async(tl_id)
            return self.get(tl_id)
        # Reflect the transition right away for a snappy Stop button.
        self._store.update_timelapse(
            tl_id,
            state="encoding",
            end_ts=time.time(),
            frames_captured=worker.frames_captured,
            width=worker.width,
            height=worker.height,
        )
        threading.Thread(
            target=self._stop_and_finalize,
            args=(tl_id,),
            name=f"timelapse-stop-{tl_id}",
            daemon=True,
        ).start()
        return self.get(tl_id)

    def _stop_and_finalize(self, tl_id: int) -> None:
        with self._lock:
            worker = self._workers.get(tl_id)
        if worker is not None:
            worker.stop()  # finish the current frame and join the capture thread
            self._store.update_timelapse(
                tl_id,
                frames_captured=worker.frames_captured,
                width=worker.width,
                height=worker.height,
            )
        self._finalize_async(tl_id)

    def encode(self, tl_id: int) -> TimelapseRecord | None:
        """(Re)encode a stopped/interrupted timelapse from its stored frames."""
        self._finalize_async(tl_id)
        return self.get(tl_id)

    def _finalize_async(self, tl_id: int) -> None:
        with self._lock:
            if tl_id in self._encoding:
                return
            self._encoding.add(tl_id)
            self._workers.pop(tl_id, None)
        self._store.update_timelapse(tl_id, state="encoding")
        threading.Thread(
            target=self._encode_job, args=(tl_id,), name=f"timelapse-encode-{tl_id}", daemon=True
        ).start()

    def _encode_job(self, tl_id: int) -> None:
        try:
            record = self._store.get_timelapse(tl_id)
            if record is None:
                return
            result = _encode_frames(Path(record.frames_dir), record.output_fps)
            if result is None:
                self._store.update_timelapse(tl_id, state="error")
                log.warning("timelapse %s: nothing to encode", tl_id)
                return
            video_path, thumb_path, (w, h, count) = result
            self._store.update_timelapse(
                tl_id,
                state="complete",
                video_path=str(video_path),
                thumb_path=str(thumb_path) if thumb_path else None,
                size_bytes=video_path.stat().st_size,
                width=w,
                height=h,
                frames_captured=count,
                end_ts=record.end_ts or time.time(),
            )
            log.info("timelapse %s encoded: %d frames -> %s", tl_id, count, video_path.name)
        except Exception as exc:  # pragma: no cover - encode failure
            log.exception("timelapse %s encode failed: %s", tl_id, exc)
            self._store.update_timelapse(tl_id, state="error")
        finally:
            with self._lock:
                self._encoding.discard(tl_id)

    # -- smoothing (ffmpeg post-processing) --------------------------------
    def smooth(
        self,
        tl_id: int,
        target_fps: int | None = None,
        interpolate: bool | None = None,
        deflicker: bool | None = None,
        engine: str | None = None,
    ) -> TimelapseRecord | None:
        """Kick off a background pass that turns the captured frames into smooth,
        flowing motion. ``engine`` is "ffmpeg" or "rife"; a failed RIFE run falls
        back to ffmpeg. Re-runnable; the source frames are kept."""
        record = self._store.get_timelapse(tl_id)
        if record is None:
            return None
        frames_dir = Path(record.frames_dir)
        if not frames_dir.exists() or not any(frames_dir.glob("*.jpg")):
            return None
        with self._lock:
            if tl_id in self._smoothing:
                return self.get(tl_id)
            self._smoothing.add(tl_id)
        cfg = self._config
        tfps = target_fps or cfg.smooth_target_fps
        interp = cfg.smooth_interpolate if interpolate is None else interpolate
        defl = cfg.smooth_deflicker if deflicker is None else deflicker
        chosen = engine or cfg.smooth_engine
        if chosen == "rife" and not rife_available(cfg.rife_path):
            chosen = "ffmpeg"  # not installed → fall back before we even start
        self._store.update_timelapse(tl_id, smooth_state="processing", smooth_path=None)
        threading.Thread(
            target=self._smooth_job,
            args=(tl_id, tfps, interp, defl, chosen),
            name=f"timelapse-smooth-{tl_id}",
            daemon=True,
        ).start()
        return self.get(tl_id)

    def _smooth_job(
        self, tl_id: int, target_fps: int, interpolate: bool, deflicker: bool, engine: str
    ) -> None:
        try:
            record = self._store.get_timelapse(tl_id)
            exe = ffmpeg_path()
            if record is None or exe is None:
                self._store.update_timelapse(tl_id, smooth_state="error")
                return
            frames_dir = Path(record.frames_dir)
            out = frames_dir.parent / "smooth.mp4"

            used = "ffmpeg"
            ok = False
            if engine == "rife":
                ok = self._smooth_with_rife(record, frames_dir, out, target_fps, deflicker, exe)
                if ok:
                    used = "rife"
                else:
                    log.warning("timelapse %s: RIFE failed, falling back to ffmpeg", tl_id)
            if not ok:
                cmd = build_smooth_command(
                    exe, frames_dir, record.output_fps, out, target_fps, interpolate, deflicker
                )
                ok = run_ffmpeg(cmd) and out.exists()

            if ok and out.exists():
                self._store.update_timelapse(
                    tl_id,
                    smooth_state="complete",
                    smooth_path=str(out),
                    smooth_size_bytes=out.stat().st_size,
                    smooth_engine=used,
                )
                log.info("timelapse %s smoothed via %s -> %s", tl_id, used, out.name)
            else:
                self._store.update_timelapse(tl_id, smooth_state="error")
        except Exception as exc:  # pragma: no cover - encoder failure
            log.exception("timelapse %s smoothing failed: %s", tl_id, exc)
            self._store.update_timelapse(tl_id, smooth_state="error")
        finally:
            with self._lock:
                self._smoothing.discard(tl_id)

    def _smooth_with_rife(
        self, record, frames_dir: Path, out: Path, target_fps: int, deflicker: bool, ffmpeg: str
    ) -> bool:
        """RIFE pipeline: interpolate frames with rife-ncnn-vulkan, then encode.
        Keeps the original cadence's wall-clock duration by scaling fps with the
        interpolation multiplier. Returns False (→ ffmpeg fallback) on any error."""
        rife = rife_path(self._config.rife_path)
        if rife is None:
            return False
        src_count = len(list(frames_dir.glob("*.jpg")))
        if src_count < 2:
            return False
        multiplier = max(2, round(target_fps / max(1, record.output_fps)))
        target_frames = src_count * multiplier
        encode_fps = record.output_fps * multiplier
        interp_dir = frames_dir.parent / "interp"
        if interp_dir.exists():
            shutil.rmtree(interp_dir, ignore_errors=True)
        interp_dir.mkdir(parents=True, exist_ok=True)
        try:
            cmd = build_rife_command(
                rife, frames_dir, interp_dir, target_frames, self._config.rife_model
            )
            if not run_rife(cmd, cwd=Path(rife).parent):
                return False
            if not any(interp_dir.iterdir()):
                return False
            # RIFE writes PNGs; encode them at the multiplied fps.
            glob = str(interp_dir / "*.png")
            enc = build_encode_command(ffmpeg, glob, encode_fps, out, deflicker)
            return run_ffmpeg(enc) and out.exists()
        finally:
            shutil.rmtree(interp_dir, ignore_errors=True)

    # -- queries -----------------------------------------------------------
    def get(self, tl_id: int) -> TimelapseRecord | None:
        record = self._store.get_timelapse(tl_id)
        return self._patch_live(record) if record else None

    def list(self, camera_id: str | None = None, limit: int = 100) -> list[TimelapseRecord]:
        return [self._patch_live(r) for r in self._store.list_timelapses(camera_id, limit)]

    def _patch_live(self, record: TimelapseRecord) -> TimelapseRecord:
        """Reflect a still-running capture's live frame count/dimensions."""
        if record.id is None:
            return record
        worker = self._workers.get(record.id)
        if worker is not None:
            record.frames_captured = worker.frames_captured
            if worker.width:
                record.width, record.height = worker.width, worker.height
        return record

    # -- delete ------------------------------------------------------------
    def delete(self, tl_id: int) -> bool:
        with self._lock:
            worker = self._workers.pop(tl_id, None)
        if worker is not None:
            worker.stop()
        record = self._store.get_timelapse(tl_id)
        if record is None:
            return False
        job_dir = paths.timelapse_dir() / str(tl_id)
        try:
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
        except OSError as exc:  # pragma: no cover
            log.warning("timelapse %s: could not remove %s: %s", tl_id, job_dir, exc)
        self._store.delete_timelapse(tl_id)
        return True

    # -- lifecycle ---------------------------------------------------------
    def shutdown(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()
            # Persist progress; startup marks these 'interrupted' so the frames
            # can be encoded later without losing the capture.
            self._store.update_timelapse(
                worker.tl_id,
                frames_captured=worker.frames_captured,
                width=worker.width,
                height=worker.height,
            )


def _encode_frames(
    frames_dir: Path, fps: int
) -> tuple[Path, Path | None, tuple[int, int, int]] | None:
    frames = sorted(frames_dir.glob("*.jpg"))
    if not frames:
        return None
    first = cv2.imread(str(frames[0]))
    if first is None:
        return None
    h, w = first.shape[:2]
    out_path = frames_dir.parent / "timelapse.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
    writer = cv2.VideoWriter(str(out_path), fourcc, float(max(1, fps)), (w, h))
    if not writer.isOpened():
        log.error("timelapse: failed to open VideoWriter at %s", out_path)
        return None
    written = 0
    for frame_path in frames:
        img = cv2.imread(str(frame_path))
        if img is None:
            continue
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h))
        writer.write(img)
        written += 1
    writer.release()
    if written == 0:
        return None
    thumb_path = _write_timelapse_thumb(first, frames_dir.parent)
    return out_path, thumb_path, (w, h, written)


def _write_timelapse_thumb(image, job_dir: Path) -> Path | None:
    try:
        h, w = image.shape[:2]
        scale = _THUMB_WIDTH / max(1, w)
        thumb = cv2.resize(image, (_THUMB_WIDTH, max(1, int(h * scale))))
        thumb_path = job_dir / "thumb.jpg"
        thumb_path.write_bytes(encode_jpeg(thumb, quality=75))
        return thumb_path
    except Exception:  # pragma: no cover
        return None
