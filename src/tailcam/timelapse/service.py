"""Timelapse lifecycle: start capture, finalize (encode), list, delete.

Capture runs in a per-job thread (:class:`TimelapseCaptureWorker`). Encoding —
turning the stored JPEG frames into an mp4 — also runs off the request thread so
a long print's many frames don't block the API. Source frames are retained after
encoding so a future phase can re-stitch them with interpolation/deflicker.
"""

from __future__ import annotations

import math
import shutil
import threading
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import cv2

from tailcam import paths
from tailcam.camera.frame import FrameBuffer
from tailcam.camera.manager import CameraManager
from tailcam.config import TimelapseConfig
from tailcam.jobs.models import JobError
from tailcam.logging_setup import get_logger
from tailcam.media.storage import ProducerWorkspace, alias, enabled, local_path
from tailcam.node import RoleDisabledError
from tailcam.persistence.models import TimelapseAnalysisEventRecord, TimelapseRecord
from tailcam.persistence.store import Store
from tailcam.storage.models import StorageError
from tailcam.streaming.encoder import encode_jpeg
from tailcam.timelapse.analyzer import TimelapseAnalysisQueue
from tailcam.timelapse.ffmpeg import (
    build_encode_command,
    build_plain_encode_command,
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
    def __init__(
        self,
        manager: CameraManager,
        store: Store,
        config: TimelapseConfig,
        analysis_queue: TimelapseAnalysisQueue | None = None,
        role_check: Callable[[], None] | None = None,
        analysis_role_check: Callable[[], None] | None = None,
        storage_service=None,
        job_service=None,
    ) -> None:
        self._manager = manager
        self._store = store
        self._config = config
        self._analysis_queue = analysis_queue
        self._role_check = role_check
        self._analysis_role_check = analysis_role_check
        self._storage_service = storage_service
        self._job_service = job_service
        self._storage_jobs: dict[int, ProducerWorkspace] = {}
        self._workers: dict[int, TimelapseCaptureWorker] = {}
        self._encoding: set[int] = set()
        self._smoothing: set[int] = set()
        self._deleting: set[int] = set()
        self._legacy_jobs: set[int] = set()
        self._lock = threading.Lock()

    # -- start -------------------------------------------------------------
    def start(
        self,
        camera_id: str,
        name: str | None = None,
        interval_seconds: float | None = None,
        output_fps: int | None = None,
        duration_seconds: float = 0.0,
        jpeg_quality: int | None = None,
        max_frames: int | None = None,
        auto_smooth: bool | None = None,
        smooth_target_fps: int | None = None,
        smooth_interpolate: bool | None = None,
        smooth_deflicker: bool | None = None,
        smooth_engine: str | None = None,
        smooth_quality: str | None = None,
        analysis_enabled: bool | None = None,
        analysis_cadence_seconds: float | None = None,
        source_host: str = "",
        buffer: FrameBuffer | None = None,
        reacquire: Callable[[], FrameBuffer | None] | None = None,
        camera_name: str | None = None,
        origin_node_id: str | None = None,
    ) -> TimelapseRecord | None:
        """Start a capture. By default frames come from this node's camera
        ``camera_id``; a storage node capturing a *peer's* camera passes the
        pulled ``buffer`` (+ ``reacquire``) and the owning ``source_host``."""
        if self._role_check is not None:
            self._role_check()
        use_storage = enabled(self._storage_service)
        if use_storage and source_host and not origin_node_id:
            raise StorageError(
                "source_identity_unavailable",
                "Delegated capture requires a verified source node identity",
            )
        analysis = self._config.analysis_enabled if analysis_enabled is None else analysis_enabled
        smooth = self._config.auto_smooth if auto_smooth is None else auto_smooth
        engine = smooth_engine or self._config.smooth_engine
        if (analysis or (smooth and engine == "rife")) and (
            self._analysis_role_check is not None and self._job_service is None
        ):
            self._analysis_role_check()
        if buffer is None:
            buffer = self._manager.get_buffer(camera_id)
            reacquire = partial(self._manager.get_buffer, camera_id)
        if buffer is None:
            return None
        interval = interval_seconds or self._config.default_interval_seconds
        fps = output_fps or self._config.default_output_fps
        capture_quality = jpeg_quality or self._config.jpeg_quality
        frame_cap = self._config.max_frames if max_frames is None else max_frames
        cam = self._manager.get(camera_id) if not source_host else None
        cam_name = camera_name or (cam.name if cam else camera_id)
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
            jpeg_quality=capture_quality,
            max_frames=frame_cap,
            auto_smooth=self._config.auto_smooth if auto_smooth is None else auto_smooth,
            smooth_target_fps=smooth_target_fps or self._config.smooth_target_fps,
            smooth_interpolate=(
                self._config.smooth_interpolate
                if smooth_interpolate is None
                else smooth_interpolate
            ),
            smooth_deflicker=(
                self._config.smooth_deflicker if smooth_deflicker is None else smooth_deflicker
            ),
            smooth_engine=smooth_engine or self._config.smooth_engine,
            smooth_quality=smooth_quality or self._config.smooth_quality,
            analysis_enabled=(
                self._config.analysis_enabled if analysis_enabled is None else analysis_enabled
            ),
            analysis_cadence_seconds=(
                analysis_cadence_seconds or self._config.analysis_cadence_seconds
            ),
            source_host=source_host,
        )
        job = self._new_storage_job(camera_id, origin_node_id) if use_storage else None
        if job is None:
            paths.require_media_root()
        tl_id = self._store.add_timelapse(record)
        if job is not None:
            self._storage_jobs[tl_id] = job
        else:
            self._legacy_jobs.add(tl_id)
        record.id = tl_id
        frames_dir = (
            job.path / "frames"
            if job is not None
            else paths.timelapse_dir() / str(tl_id) / "frames"
        )
        record.frames_dir = str(frames_dir)
        self._store.update_timelapse(tl_id, frames_dir=str(frames_dir))

        # Analyze every Nth captured frame — constant per capture, so compute once.
        analysis_every = max(1, math.ceil(record.analysis_cadence_seconds / interval))
        analysis_enabled = record.analysis_enabled
        worker = TimelapseCaptureWorker(
            tl_id,
            camera_id,
            buffer,
            frames_dir,
            interval_seconds=interval,
            jpeg_quality=record.jpeg_quality,
            max_frames=record.max_frames,
            duration_seconds=duration_seconds,
            on_frame=lambda n: self._on_frame(
                tl_id, n, frames_dir, analysis_every, analysis_enabled
            ),
            on_complete=lambda: self._finalize_async(tl_id),
            reacquire=reacquire,
            save_frame=(
                lambda path, data, index: self._save_storage_frame(tl_id, job, path, data, index)
            )
            if job is not None
            else None,
        )
        with self._lock:
            self._workers[tl_id] = worker
        worker.start()
        log.info("timelapse %s started on %s (interval=%.1fs)", tl_id, camera_id, interval)
        return record

    def _new_storage_job(
        self, camera_id: str, origin_node_id: str | None = None
    ) -> ProducerWorkspace:
        return ProducerWorkspace(
            self._storage_service,
            ("timelapse_frame", "timelapse_video", "timelapse_smooth", "thumbnail"),
            camera_id,
            origin_node_id=origin_node_id,
        )

    def _save_storage_frame(
        self,
        tl_id: int,
        job: ProducerWorkspace,
        path: Path,
        data: bytes,
        index: int,
    ) -> None:
        # The source remains in a finite workspace for encoding; the cataloged
        # frame survives workspace cleanup and process interruption.
        job.check(len(data))
        artifact = job.put(
            "timelapse_frame",
            data,
            mime_type="image/jpeg",
            metadata={"timelapse_id": tl_id, "frame_index": index},
        )
        alias(job.service, "timelapse", tl_id, f"frame/{index:06d}", artifact)
        job.write(path, data)

    def _prepare_storage_frames(
        self, tl_id: int, record: TimelapseRecord
    ) -> ProducerWorkspace | None:
        if tl_id in self._legacy_jobs:
            return None
        existing = self._storage_jobs.get(tl_id)
        if existing is not None:
            return existing
        service = self._storage_service
        if service is None:
            return None
        aliases = service.catalog.aliases("timelapse", str(tl_id))
        frame_aliases = [a for a in aliases if a["variant"].startswith("frame/")]
        if not enabled(service) and not frame_aliases:
            return None
        origin_node_id = None
        if frame_aliases:
            original = service.catalog.get(frame_aliases[0]["artifact_id"])
            origin_node_id = original.origin_node_id if original is not None else None
        job = self._new_storage_job(record.camera_id, origin_node_id)
        frames = job.path / "frames"
        frames.mkdir()
        if frame_aliases:
            for entry in sorted(frame_aliases, key=lambda item: item["variant"]):
                index = entry["variant"].split("/", 1)[1]
                if not index.isdecimal() or len(index) != 6:
                    raise ValueError("Invalid cataloged timelapse frame index")
                source = service.materialize(entry["artifact_id"], job.lease)
                target = frames / f"{index}.jpg"
                job.check(source.stat().st_size)
                shutil.copyfile(source, target)
        else:
            # Reencoding legacy frames is a new job, with a newly admitted
            # destination. Never let an encoder write beside an old mount.
            for source in sorted(Path(record.frames_dir).glob("*.jpg")):
                job.check(source.stat().st_size)
                shutil.copyfile(source, frames / source.name)
        self._storage_jobs[tl_id] = job
        self._store.update_timelapse(tl_id, frames_dir=str(frames))
        return job

    def _on_frame(
        self,
        tl_id: int,
        n: int,
        frames_dir: Path,
        analysis_every: int,
        analysis_enabled: bool,
    ) -> None:
        # Persist progress occasionally so a crash loses little; live reads use
        # the in-memory worker counter (see _patch_live).
        if n % 10 == 0:
            self._store.update_timelapse(tl_id, frames_captured=n)
        if (
            analysis_enabled
            and self._analysis_queue is not None
            and (n == 1 or n % analysis_every == 0)
        ):
            evidence = frames_dir / f"{n - 1:06d}.jpg"
            try:
                self._analysis_queue.submit(tl_id, n - 1, evidence)
            except (JobError, RoleDisabledError):
                # Analysis placement can change during a long capture. Its
                # refusal must never escape into the camera's frame loop.
                self._store.add_timelapse_analysis_event(
                    TimelapseAnalysisEventRecord(
                        id=None, timelapse_id=tl_id, frame_number=n - 1,
                        state="uncertain", confidence=0.0,
                        description="Printer analysis placement unavailable for this frame",
                        evidence_path=str(evidence), created_ts=time.time(),
                    )
                )

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
            if worker.alive:
                log.warning(
                    "timelapse %s: capture has not stopped; retaining frames for recovery", tl_id
                )
                self._store.update_timelapse(tl_id, state="error")
                return
            self._store.update_timelapse(
                tl_id,
                frames_captured=worker.frames_captured,
                width=worker.width,
                height=worker.height,
            )
        try:
            self._finalize_async(tl_id)
        except Exception:
            # The source record remains retryable; never leave an uncaught stop-thread error.
            self._store.update_timelapse(tl_id, state="error")
            log.warning("timelapse %s: processing admission failed; frames retained", tl_id)

    def encode(self, tl_id: int) -> TimelapseRecord | None:
        """(Re)encode a stopped/interrupted timelapse from its stored frames."""
        with self._lock:
            worker = self._workers.get(tl_id)
        if worker is not None and worker.alive:
            return self.stop(tl_id)  # join the producer before giving an encoder its workspace
        self._finalize_async(tl_id)
        return self.get(tl_id)

    def _finalize_async(self, tl_id: int) -> None:
        if self._role_check is not None and self._job_service is None:
            self._role_check()
        record = self._store.get_timelapse(tl_id)
        if record is None:
            return
        with self._lock:
            if tl_id in self._encoding or tl_id in self._deleting:
                return
            self._encoding.add(tl_id)
        try:
            if self._job_service is None:
                self._prepare_storage_frames(tl_id, record)
        except Exception:
            with self._lock:
                self._encoding.discard(tl_id)
            raise
        with self._lock:
            completed_worker = self._workers.pop(tl_id, None)
        if completed_worker is not None:
            self._store.update_timelapse(
                tl_id, frames_captured=completed_worker.frames_captured,
                width=completed_worker.width, height=completed_worker.height,
            )
        self._store.update_timelapse(tl_id, state="encoding")
        if self._job_service is not None:
            try:
                from tailcam.workloads.timelapse import submit

                submit(self, tl_id, "timelapse_encode", {"fps": record.output_fps})
            except Exception:
                with self._lock:
                    self._encoding.discard(tl_id)
                self._store.update_timelapse(tl_id, state="error")
                raise
            return
        threading.Thread(
            target=self._encode_job, args=(tl_id,), name=f"timelapse-encode-{tl_id}", daemon=True
        ).start()

    def _encode_job(self, tl_id: int) -> None:
        completed = False
        try:
            record = self._store.get_timelapse(tl_id)
            if record is None:
                return
            job = self._storage_jobs.get(tl_id)
            if job is not None:
                job.check()
            result = _encode_frames(Path(record.frames_dir), record.output_fps)
            if job is not None:
                job.check()
            if result is None and not any(Path(record.frames_dir).glob("*.jpg")):
                # Interrupted before a single frame landed — nothing to keep.
                self._store.update_timelapse(tl_id, state="error")
                log.warning("timelapse %s: no frames were captured", tl_id)
                return
            if result is None:
                self._store.update_timelapse(tl_id, state="error")
                log.warning("timelapse %s: nothing to encode", tl_id)
                return
            video_path, thumb_path, (w, h, count) = result
            size_bytes = video_path.stat().st_size
            saved_video, saved_thumb = str(video_path), str(thumb_path) if thumb_path else None
            if job is not None:
                artifact = job.finish(
                    video_path,
                    "timelapse_video",
                    mime_type="video/mp4",
                    metadata={"timelapse_id": tl_id},
                )
                alias(job.service, "timelapse", tl_id, "video", artifact)
                saved_video = local_path(job.service, artifact)
                if thumb_path is not None:
                    thumb = job.finish(
                        thumb_path,
                        "thumbnail",
                        mime_type="image/jpeg",
                        parent_id=artifact.artifact_id,
                    )
                    alias(job.service, "timelapse", tl_id, "thumbnail", thumb)
                    saved_thumb = local_path(job.service, thumb) or None
            self._store.update_timelapse(
                tl_id,
                state="complete",
                video_path=saved_video,
                thumb_path=saved_thumb,
                size_bytes=size_bytes,
                width=w,
                height=h,
                frames_captured=count,
                end_ts=record.end_ts or time.time(),
            )
            completed = True
            log.info("timelapse %s encoded: %d frames -> %s", tl_id, count, video_path.name)
            if record.auto_smooth:
                self.smooth(
                    tl_id,
                    target_fps=record.smooth_target_fps,
                    interpolate=record.smooth_interpolate,
                    deflicker=record.smooth_deflicker,
                    engine=record.smooth_engine,
                    quality=record.smooth_quality,
                )
        except Exception as exc:  # pragma: no cover - encode failure
            log.exception("timelapse %s encode failed: %s", tl_id, exc)
            self._store.update_timelapse(tl_id, state="error")
        finally:
            with self._lock:
                self._encoding.discard(tl_id)
                smoothing = tl_id in self._smoothing
            if completed and not smoothing:
                self._legacy_jobs.discard(tl_id)
                job = self._storage_jobs.pop(tl_id, None)
                if job is not None:
                    job.release()

    # -- smoothing (ffmpeg post-processing) --------------------------------
    def smooth(
        self,
        tl_id: int,
        target_fps: int | None = None,
        interpolate: bool | None = None,
        deflicker: bool | None = None,
        engine: str | None = None,
        quality: str | None = None,
    ) -> TimelapseRecord | None:
        with self._lock:
            if tl_id in self._deleting:
                return None
            worker = self._workers.get(tl_id)
            if worker is not None and worker.alive:
                raise StorageError(
                    "capture_busy", "Stop timelapse capture before smoothing its frames"
                )
            if tl_id in self._smoothing:
                return self.get(tl_id)
            self._smoothing.add(tl_id)
        try:
            result = self._start_smoothing(
                tl_id, target_fps, interpolate, deflicker, engine, quality
            )
            if result is None:
                with self._lock:
                    self._smoothing.discard(tl_id)
            return result
        except Exception:
            with self._lock:
                self._smoothing.discard(tl_id)
            raise

    def _start_smoothing(
        self,
        tl_id: int,
        target_fps: int | None = None,
        interpolate: bool | None = None,
        deflicker: bool | None = None,
        engine: str | None = None,
        quality: str | None = None,
    ) -> TimelapseRecord | None:
        """Kick off a background pass that turns the captured frames into smooth,
        flowing motion. ``engine`` is "ffmpeg" or "rife"; a failed RIFE run falls
        back to ffmpeg. Re-runnable; the source frames are kept."""
        if self._role_check is not None and self._job_service is None:
            self._role_check()
        record = self._store.get_timelapse(tl_id)
        if record is None:
            return None
        if self._job_service is not None:
            from tailcam.workloads.timelapse import submit

            parameters = {
                "fps": record.output_fps, "target_fps": target_fps or record.smooth_target_fps,
                "interpolate": record.smooth_interpolate if interpolate is None else interpolate,
                "deflicker": record.smooth_deflicker if deflicker is None else deflicker,
                "engine": engine or record.smooth_engine or "ffmpeg",
                "quality": quality or record.smooth_quality,
            }
            submit(self, tl_id, "timelapse_interpolate", parameters)
            self._store.update_timelapse(tl_id, smooth_state="processing")
            return self.get(tl_id)
        chosen = engine or record.smooth_engine
        if (chosen == "rife" and self._analysis_role_check is not None
                and self._job_service is None):
            self._analysis_role_check()
        job = self._prepare_storage_frames(tl_id, record)
        if job is not None:
            record = self._store.get_timelapse(tl_id) or record
            job.check()
        frames_dir = Path(record.frames_dir)
        if not frames_dir.exists() or not any(frames_dir.glob("*.jpg")):
            return None
        tfps = target_fps or record.smooth_target_fps
        interp = record.smooth_interpolate if interpolate is None else interpolate
        defl = record.smooth_deflicker if deflicker is None else deflicker
        chosen = engine or record.smooth_engine
        output_quality = quality or record.smooth_quality
        if chosen == "rife" and not rife_available(self._config.rife_path):
            chosen = "ffmpeg"  # not installed → fall back before we even start
        self._store.update_timelapse(
            tl_id,
            smooth_state="processing",
            smooth_target_fps=tfps,
            smooth_interpolate=int(interp),
            smooth_deflicker=int(defl),
            smooth_engine=chosen,
            smooth_quality=output_quality,
        )
        threading.Thread(
            target=self._smooth_job,
            args=(tl_id, tfps, interp, defl, chosen, output_quality),
            name=f"timelapse-smooth-{tl_id}",
            daemon=True,
        ).start()
        return self.get(tl_id)

    def _smooth_job(
        self,
        tl_id: int,
        target_fps: int,
        interpolate: bool,
        deflicker: bool,
        engine: str,
        quality: str,
    ) -> None:
        pending: Path | None = None
        completed = False
        try:
            record = self._store.get_timelapse(tl_id)
            exe = ffmpeg_path()
            if record is None or exe is None:
                self._store.update_timelapse(tl_id, smooth_state="error")
                return
            frames_dir = Path(record.frames_dir)
            out = frames_dir.parent / "smooth.mp4"
            pending = frames_dir.parent / "smooth.pending.mp4"
            pending.unlink(missing_ok=True)

            used = "ffmpeg"
            ok = False
            if engine == "rife":
                ok = self._smooth_with_rife(
                    record, frames_dir, pending, target_fps, deflicker, quality, exe
                )
                if ok:
                    used = "rife"
                else:
                    log.warning("timelapse %s: RIFE failed, falling back to ffmpeg", tl_id)
            if not ok:
                cmd = build_smooth_command(
                    exe,
                    frames_dir,
                    record.output_fps,
                    pending,
                    target_fps,
                    interpolate,
                    deflicker,
                    quality,
                )
                ok = run_ffmpeg(cmd) and pending.exists()

            if ok and pending.exists():
                pending.replace(out)
                saved_path = str(out)
                saved_size = out.stat().st_size
                job = self._storage_jobs.get(tl_id)
                if job is not None:
                    artifact = job.finish(
                        out,
                        "timelapse_smooth",
                        mime_type="video/mp4",
                        metadata={"timelapse_id": tl_id},
                    )
                    alias(job.service, "timelapse", tl_id, "smooth", artifact)
                    saved_path = local_path(job.service, artifact)
                self._store.update_timelapse(
                    tl_id,
                    smooth_state="complete",
                    smooth_path=saved_path,
                    smooth_size_bytes=saved_size,
                    smooth_engine=used,
                )
                completed = True
                log.info("timelapse %s smoothed via %s -> %s", tl_id, used, out.name)
            else:
                self._store.update_timelapse(tl_id, smooth_state="error")
        except Exception as exc:  # pragma: no cover - encoder failure
            log.exception("timelapse %s smoothing failed: %s", tl_id, exc)
            self._store.update_timelapse(tl_id, smooth_state="error")
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
            with self._lock:
                self._smoothing.discard(tl_id)
            if completed:
                self._legacy_jobs.discard(tl_id)
                job = self._storage_jobs.pop(tl_id, None)
                if job is not None:
                    job.release()

    def _smooth_with_rife(
        self,
        record,
        frames_dir: Path,
        out: Path,
        target_fps: int,
        deflicker: bool,
        quality: str,
        ffmpeg: str,
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
            enc = build_encode_command(ffmpeg, glob, encode_fps, out, deflicker, quality)
            return run_ffmpeg(enc) and out.exists()
        finally:
            shutil.rmtree(interp_dir, ignore_errors=True)

    # -- queries -----------------------------------------------------------
    def get(self, tl_id: int) -> TimelapseRecord | None:
        from tailcam.workloads.timelapse import project

        project(self, tl_id)
        record = self._store.get_timelapse(tl_id)
        return self._patch_live(record) if record else None

    def list(self, camera_id: str | None = None, limit: int = 100) -> list[TimelapseRecord]:
        return [current for record in self._store.list_timelapses(camera_id, limit)
                if record.id is not None and (current := self.get(record.id)) is not None]

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
        from tailcam.workloads.timelapse import project

        project(self, tl_id)
        with self._lock:
            if tl_id in self._encoding or tl_id in self._smoothing or tl_id in self._deleting:
                return False  # active encoders still own their files and reservation
            self._deleting.add(tl_id)
            worker = self._workers.get(tl_id)
        try:
            if worker is not None:
                worker.stop()
                if worker.alive:
                    return False
                with self._lock:
                    self._workers.pop(tl_id, None)
            record = self._store.get_timelapse(tl_id)
            if record is None:
                return False
            service = self._storage_service
            if service is not None and service.catalog.aliases("timelapse", str(tl_id)):
                service.delete_family("timelapse", str(tl_id))
            job_dir = (
                Path(record.frames_dir).parent
                if record.frames_dir
                else paths.timelapse_dir() / str(tl_id)
            )
            job = self._storage_jobs.get(tl_id)
            if job is not None:
                job.release()  # only drops reservation after verified successful cleanup
                self._storage_jobs.pop(tl_id, None)
            elif service is not None and service.release_workspace(job_dir):
                pass  # durable ownership survives a process restart
            else:
                if not job_dir.parent.is_dir() or job_dir.is_symlink():
                    return False
                if job_dir.exists():
                    shutil.rmtree(job_dir)
            self._store.delete_timelapse(tl_id)
            self._legacy_jobs.discard(tl_id)
            return True
        except (OSError, StorageError) as exc:
            log.warning("timelapse %s: could not delete its files: %s", tl_id, exc)
            return False
        finally:
            with self._lock:
                self._deleting.discard(tl_id)

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
        if self._analysis_queue is not None:
            self._analysis_queue.shutdown()


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
    written = _encode_with_ffmpeg(frames_dir, fps, out_path, (w, h), len(frames))
    if written == 0:
        written = _encode_with_opencv(frames, fps, out_path, (w, h))
    if written == 0:
        return None
    thumb_path = _write_timelapse_thumb(first, frames_dir.parent)
    return out_path, thumb_path, (w, h, written)


def _encode_with_ffmpeg(
    frames_dir: Path, fps: int, out_path: Path, size: tuple[int, int], count: int
) -> int:
    """H.264 (browser-playable) via ffmpeg. Returns frames encoded, 0 on failure."""
    exe = ffmpeg_path()
    if exe is None:
        return 0
    pending = out_path.with_suffix(".pending.mp4")
    pending.unlink(missing_ok=True)
    ok = run_ffmpeg(build_plain_encode_command(exe, frames_dir, fps, pending, size))
    if ok and pending.exists() and pending.stat().st_size > 0:
        pending.replace(out_path)
        return count
    pending.unlink(missing_ok=True)
    log.warning("timelapse: ffmpeg encode failed; falling back to OpenCV writer")
    return 0


def _encode_with_opencv(frames: list[Path], fps: int, out_path: Path, size: tuple[int, int]) -> int:
    from tailcam.media.video_sink import OpenCVSink

    w, h = size
    sink = OpenCVSink(out_path, float(max(1, fps)), (w, h))
    if not sink.opened:
        log.error("timelapse: failed to open VideoWriter at %s", out_path)
        return 0
    written = 0
    for frame_path in frames:
        img = cv2.imread(str(frame_path))
        if img is None:
            continue
        sink.write(img)
        written += 1
    sink.close()
    return written


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
