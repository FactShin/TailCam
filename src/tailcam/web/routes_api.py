"""REST API endpoints under /api."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tailcam import __version__
from tailcam.camera.manager import ManagedCamera
from tailcam.timelapse.presets import printer_presets
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.schemas import (
    AIInfo,
    AIModelRequest,
    AIPullStatus,
    AITestRequest,
    AITestResult,
    AIUpdate,
    AnnotationBox,
    CameraInfo,
    CameraSettingsUpdate,
    ChannelEntry,
    CollectionUpdate,
    DatasetCreate,
    DatasetInfo,
    DetectionBox,
    DetectionInfo,
    DetectionResult,
    DetectionUpdate,
    EngineInfo,
    FsBrowseInfo,
    FsEntry,
    HACameraEntry,
    HomeAssistantStatus,
    HomeAssistantUpdate,
    HomeKitStatus,
    HomeKitUpdate,
    HostInfo,
    ImageAnalysisResult,
    InstalledPluginEntry,
    IntegrationsInfo,
    MarketPluginEntry,
    McpInfo,
    McpUpdate,
    MediaCreatedResponse,
    MediaInfo,
    ModelInfo,
    ModelRegister,
    MotionEventInfo,
    NotificationsInfo,
    NotificationsUpdate,
    OkResponse,
    OllamaModelsInfo,
    PipelineInfo,
    PluginEntry,
    PluginInstallRequest,
    PluginsInfo,
    PluginsMarketInfo,
    PluginToggleRequest,
    PostprocessInfo,
    PostprocessSettings,
    ProviderEntry,
    SampleAnnotations,
    SampleAnnotationsUpdate,
    SampleInfo,
    SampleRelabel,
    StorageInfo,
    StorageNodeInfo,
    StorageUpdate,
    StreamingDefaults,
    StreamingDefaultsUpdate,
    StreamOverrides,
    StreamSettingsModel,
    SystemInfo,
    TimelapseAnalysisEventInfo,
    TimelapseInfo,
    TimelapseSmoothRequest,
    TimelapseStartRequest,
    TrainingInfo,
    TrainingRunInfo,
    TrainRequest,
    TransformModel,
    UpdateInfo,
)

router = APIRouter(prefix="/api")


def _camera_info(ctx: AppContext, cam: ManagedCamera) -> CameraInfo:
    worker = cam.worker
    status = worker.state.status.value if worker else "offline"
    fps = worker.state.fps if worker else 0.0
    return CameraInfo(
        id=cam.descriptor.id,
        name=cam.name,
        backend=cam.descriptor.backend,
        status=status,
        fps=fps,
        width=cam.properties.width,
        height=cam.properties.height,
        recording=ctx.capture.is_recording(cam.descriptor.id),
        motion_enabled=ctx.motion_enabled(cam.descriptor.id),
        properties=cam.properties.to_dict(),
        transform=TransformModel(
            rotation=cam.transform.rotation,
            flip_h=cam.transform.flip_h,
            flip_v=cam.transform.flip_v,
        ),
        stream=StreamSettingsModel(**cam.effective_stream(ctx.config)),
        stream_overrides=StreamOverrides(**cam.stream),
        detection_enabled=ctx.manager.detection_enabled_for(cam.descriptor.id),
        detection_override=cam.detection_enabled,
        last_error=worker.state.last_error if worker else None,
        host=ctx.local_host,
        proxy_prefix="",
    )


async def _aggregate_cameras(ctx: AppContext, scope: str) -> list[CameraInfo]:
    local = [_camera_info(ctx, cam) for cam in ctx.manager.list()]
    if scope == "local":
        return local
    # Remote cameras arrive as CameraInfo-shaped dicts already tagged with the
    # peer's host + proxy_prefix; validate them into the response model.
    remote = [CameraInfo.model_validate(c) for c in await ctx.cluster.remote_cameras()]
    return local + remote


@router.get("/cameras", response_model=list[CameraInfo])
async def list_cameras(
    scope: str = Query("all", pattern="^(all|local)$"),
    ctx: AppContext = Depends(get_context),
) -> list[CameraInfo]:
    """List cameras. ``scope=local`` returns only this node's cameras (peers use
    this to avoid recursive aggregation); ``all`` (default) includes peers."""
    return await _aggregate_cameras(ctx, scope)


@router.post("/cameras/refresh", response_model=list[CameraInfo])
async def refresh_cameras(
    scope: str = Query("all", pattern="^(all|local)$"),
    ctx: AppContext = Depends(get_context),
) -> list[CameraInfo]:
    import anyio

    # Discovery opens devices (seconds on macOS/Windows): keep it off the loop.
    await anyio.to_thread.run_sync(_rescan_local, ctx)
    if scope != "local":
        await ctx.cluster.refresh(force=True)
    return await _aggregate_cameras(ctx, scope)


@router.post("/cameras/restore-hidden", response_model=list[CameraInfo])
async def restore_hidden(ctx: AppContext = Depends(get_context)) -> list[CameraInfo]:
    """Un-hide every deleted/forgotten camera and re-scan."""
    import anyio

    if ctx.config.cameras.hidden:
        ctx.config.cameras.hidden.clear()
        await anyio.to_thread.run_sync(ctx.config.save)
    await anyio.to_thread.run_sync(_rescan_local, ctx)
    return await _aggregate_cameras(ctx, "all")


def _rescan_local(ctx: AppContext) -> None:
    ctx.manager.discover()
    ctx.manager.start_all()


def _restart_all_local(ctx: AppContext) -> None:
    for cam in ctx.manager.list():
        ctx.manager.restart(cam.descriptor.id)
    _rescan_local(ctx)


@router.get("/hosts", response_model=list[HostInfo])
async def list_hosts(ctx: AppContext = Depends(get_context)) -> list[HostInfo]:
    hosts = [
        HostInfo(
            host=ctx.local_host,
            node_key="local",
            kind="local",
            online=True,
            version=__version__,
            camera_count=len(ctx.manager.list()),
            proxy_prefix="",
        )
    ]
    for peer in await ctx.cluster.peers():
        hosts.append(
            HostInfo(
                host=peer.host,
                node_key=peer.key,
                kind="peer",
                online=peer.online,
                version=peer.version,
                camera_count=peer.camera_count,
                proxy_prefix=f"/proxy/{peer.key}",
            )
        )
    return hosts


@router.get("/cameras/{camera_id:path}", response_model=CameraInfo)
def get_camera(camera_id: str, ctx: AppContext = Depends(get_context)) -> CameraInfo:
    cam = ctx.manager.get(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return _camera_info(ctx, cam)


@router.patch("/cameras/{camera_id:path}", response_model=CameraInfo)
def update_camera(
    camera_id: str, update: CameraSettingsUpdate, ctx: AppContext = Depends(get_context)
) -> CameraInfo:
    cam = ctx.manager.get(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")

    if update.name is not None:
        ctx.manager.rename(camera_id, update.name)

    settings: dict = {}
    if update.properties is not None:
        settings["properties"] = update.properties.model_dump(exclude_none=True)
    if update.transform is not None:
        settings["transform"] = update.transform.model_dump()
    if update.stream is not None:
        # Only fields the client sent change; an explicit null clears an override.
        settings["stream"] = update.stream.model_dump(exclude_unset=True)
    if update.clear_detection_override:
        settings["detection_enabled"] = None
    elif update.detection_enabled is not None:
        settings["detection_enabled"] = update.detection_enabled
    if settings:
        ctx.manager.update_settings(camera_id, settings)

    if update.motion_enabled is not None:
        if update.motion_enabled:
            ctx.enable_motion(camera_id)
        else:
            ctx.disable_motion(camera_id)

    return _camera_info(ctx, cam)


@router.post("/cameras/{camera_id:path}/restart", response_model=OkResponse)
def restart_camera(camera_id: str, ctx: AppContext = Depends(get_context)) -> OkResponse:
    """Recover a stuck feed by restarting its capture worker."""
    if not ctx.manager.restart(camera_id):
        raise HTTPException(status_code=404, detail="camera not found")
    return OkResponse(detail="camera restarting")


@router.delete("/cameras/{camera_id:path}", response_model=OkResponse)
def delete_camera(camera_id: str, ctx: AppContext = Depends(get_context)) -> OkResponse:
    """Forget a camera: stop it, drop its record, and hide it from discovery
    (so phantom devices stay gone). A physically reconnected device can be
    brought back with 'Restore hidden' / refresh."""
    ctx.disable_motion(camera_id)
    if not ctx.manager.remove(camera_id):
        raise HTTPException(status_code=404, detail="camera not found")
    return OkResponse(detail="camera removed")


@router.post("/cameras/{camera_id:path}/snapshot", response_model=MediaCreatedResponse)
def snapshot(camera_id: str, ctx: AppContext = Depends(get_context)) -> MediaCreatedResponse:
    record = ctx.snapshots.capture(camera_id)
    if record is None:
        raise HTTPException(status_code=503, detail="could not capture snapshot")
    return MediaCreatedResponse(media_id=record.id)


@router.post("/cameras/{camera_id:path}/recording/start", response_model=OkResponse)
def start_recording(camera_id: str, ctx: AppContext = Depends(get_context)) -> OkResponse:
    """Record this camera — here, or on the configured storage node."""
    started = ctx.capture.start_recording(camera_id)
    if not started:
        raise HTTPException(status_code=409, detail="already recording or camera unavailable")
    where = ctx.capture.configured_node if ctx.capture.target() else "this device"
    return OkResponse(detail=f"recording started on {where}")


@router.post("/cameras/{camera_id:path}/recording/stop", response_model=MediaCreatedResponse)
def stop_recording(camera_id: str, ctx: AppContext = Depends(get_context)) -> MediaCreatedResponse:
    record = ctx.capture.stop_recording(camera_id)
    if record is None:
        raise HTTPException(status_code=409, detail="not recording")
    return MediaCreatedResponse(media_id=record.id)


# One inference per camera per this many seconds is shared by every viewer.
_DETECT_CACHE_SECONDS = 1.0


@router.post("/cameras/{camera_id:path}/detect", response_model=DetectionResult)
async def detect_objects(
    camera_id: str, ctx: AppContext = Depends(get_context)
) -> DetectionResult:
    """Run the active detection model on the camera's latest frame and return
    bounding boxes (where + what). 200 with ``detector_active=false`` when no
    detection model is active or detection is off for this camera — the UI
    just shows no overlay. Results are cached ~1s per camera so several open
    viewers cost one inference."""
    import anyio

    # Cheap early-returns first: this endpoint is polled ~every 1.5s per open
    # viewer.
    if not ctx.manager.detection_enabled_for(camera_id):
        return DetectionResult(camera_id=camera_id, detector_active=False)
    if not ctx.inference.detection_active:
        return DetectionResult(camera_id=camera_id, detector_active=False)
    cached = ctx.cached_detection(camera_id, _DETECT_CACHE_SECONDS)
    if cached is not None:
        return cached  # type: ignore[return-value]
    buffer = ctx.manager.get_buffer(camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="camera not found")

    def _run() -> DetectionResult:
        with ctx.detect_lock(camera_id):
            again = ctx.cached_detection(camera_id, _DETECT_CACHE_SECONDS)
            if again is not None:
                return again  # type: ignore[return-value]
            frame = buffer.await_latest(-1, 2.0)
            if frame is None:
                raise HTTPException(status_code=503, detail="no frame available")
            detections = ctx.inference.detect(frame.image)
            result = DetectionResult(
                camera_id=camera_id,
                detector_active=True,
                model_name=_detection_model_name(ctx),
                boxes=[
                    DetectionBox(
                        label=d.label, confidence=d.confidence, cx=d.cx, cy=d.cy, w=d.w, h=d.h
                    )
                    for d in (detections or [])
                ],
                note=ctx.inference.detection_note(),
            )
            ctx.store_detection(camera_id, result)
            return result

    return await anyio.to_thread.run_sync(_run)


def _detection_model_name(ctx: AppContext) -> str | None:
    active_model = ctx.store.active_model()
    if active_model is not None and getattr(active_model, "task", "") == "detection":
        return active_model.name
    remote = ctx.remote_detector()
    if remote is not None:
        return remote.model_name()
    return ctx.detector.status().model or None


@router.post("/detect-image", response_model=ImageAnalysisResult)
async def detect_image(
    request: Request,
    mode: str = Query("detect", pattern="^(detect|analyze)$"),
    ctx: AppContext = Depends(get_context),
) -> ImageAnalysisResult:
    """Detect objects in (``mode=detect``) or label (``mode=analyze``) one
    uploaded JPEG/PNG, using THIS node's pipeline. Peers whose ``[detection]
    node`` points here send their frames to this endpoint, so a Pi can stream
    while a bigger box runs the models."""
    import anyio
    import cv2
    import numpy as np

    body = await request.body()
    if not body or len(body) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="image body required (max 12 MB)")
    image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="could not decode image")
    if mode == "analyze":
        analysis = await anyio.to_thread.run_sync(ctx.inference.analyze, image)
        return ImageAnalysisResult(
            detector_active=ctx.inference.enabled,
            model_name=_detection_model_name(ctx),
            label=analysis.label if analysis else None,
            description=analysis.description if analysis else None,
            confidence=analysis.confidence if analysis else None,
        )
    if not ctx.inference.detection_active:
        return ImageAnalysisResult(detector_active=False)
    detections = await anyio.to_thread.run_sync(ctx.inference.detect, image)
    return ImageAnalysisResult(
        detector_active=True,
        model_name=_detection_model_name(ctx),
        boxes=[
            DetectionBox(label=d.label, confidence=d.confidence, cx=d.cx, cy=d.cy, w=d.w, h=d.h)
            for d in (detections or [])
        ],
    )


@router.get("/detection", response_model=DetectionInfo)
def detection_info(ctx: AppContext = Depends(get_context)) -> DetectionInfo:
    """Built-in object detection status: engine, model, download progress."""
    from tailcam import hostinfo

    s = ctx.detector.status()
    cfg = ctx.config.detection
    remote = ctx.remote_detector()
    remote_status = remote.status() if remote else None
    return DetectionInfo(
        enabled=s.enabled, engine=s.engine, model=s.model, status=s.status,
        percent=s.percent, detail=s.detail, error=s.error,
        confidence=cfg.confidence, classes=cfg.classes,
        overlay_default=cfg.overlay_default,
        node=cfg.node,
        node_reachable=bool(remote_status["reachable"]) if remote_status else True,
        node_error=str(remote_status["error"]) if remote_status else "",
        low_power_host=hostinfo.is_low_power(),
    )


@router.post("/detection", response_model=DetectionInfo)
def update_detection(
    update: DetectionUpdate, ctx: AppContext = Depends(get_context)
) -> DetectionInfo:
    """Change built-in detection settings (persisted; takes effect immediately)."""
    cfg = ctx.config.detection
    if update.node is not None:
        cfg.node = update.node.strip()
        # Clear stale per-camera results so the new source takes over at once.
        ctx._detect_cache.clear()
    if update.enabled is not None:
        cfg.enabled = update.enabled
        if update.enabled and not cfg.node:
            ctx.detector.ensure_ready()
    if update.confidence is not None:
        cfg.confidence = max(0.05, min(0.95, update.confidence))
    if update.classes is not None:
        cfg.classes = [c.strip() for c in update.classes if c.strip()]
    if update.overlay_default is not None:
        cfg.overlay_default = update.overlay_default
    ctx.config.save()
    return detection_info(ctx)


def _media_info(ctx: AppContext, record) -> MediaInfo:
    return MediaInfo(
        id=record.id,
        camera_id=record.camera_id,
        media_type=record.media_type,
        created_ts=record.created_ts,
        trigger=record.trigger,
        size_bytes=record.size_bytes,
        has_thumbnail=bool(record.thumbnail),
        host=ctx.local_host,
        proxy_prefix="",
        source_host=record.source_host,
    )


@router.get("/media", response_model=list[MediaInfo])
async def list_media(
    camera_id: str | None = None,
    media_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    scope: str = Query("all", pattern="^(all|local)$"),
    ctx: AppContext = Depends(get_context),
) -> list[MediaInfo]:
    local = [_media_info(ctx, r) for r in ctx.gallery.list(camera_id, media_type, limit + offset)]
    if scope == "local":
        return local[offset:]
    params = {"camera_id": camera_id, "media_type": media_type, "limit": limit + offset}
    remote = [MediaInfo.model_validate(m) for m in await ctx.cluster.remote_media(params)]
    merged = sorted(local + remote, key=lambda m: m.created_ts, reverse=True)
    return merged[offset : offset + limit]


@router.delete("/media/{media_id}", response_model=OkResponse)
def delete_media(media_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    if not ctx.gallery.delete(media_id):
        raise HTTPException(status_code=404, detail="media not found")
    return OkResponse(detail="deleted")


def _event_info(ctx: AppContext, e) -> MotionEventInfo:
    return MotionEventInfo(
        id=e.id,
        camera_id=e.camera_id,
        start_ts=e.start_ts,
        end_ts=e.end_ts,
        peak_score=e.peak_score,
        recording_id=e.recording_id,
        label=e.label,
        description=e.description,
        confidence=e.confidence,
        has_thumb=bool(e.thumb_path),
        host=ctx.local_host,
        proxy_prefix="",
    )


@router.get("/events", response_model=list[MotionEventInfo])
async def list_events(
    camera_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    scope: str = Query("all", pattern="^(all|local)$"),
    ctx: AppContext = Depends(get_context),
) -> list[MotionEventInfo]:
    local = [_event_info(ctx, e) for e in ctx.event_log.list(camera_id, limit + offset)]
    if scope == "local":
        return local[offset:]
    params = {"camera_id": camera_id, "limit": limit + offset}
    remote = [MotionEventInfo.model_validate(e) for e in await ctx.cluster.remote_events(params)]
    merged = sorted(local + remote, key=lambda e: e.start_ts, reverse=True)
    return merged[offset : offset + limit]


# -- timelapse -------------------------------------------------------------


def _timelapse_info(
    ctx: AppContext, r, summary: tuple[int, str] | None = None
) -> TimelapseInfo:
    # ``summary`` lets the list endpoint pass a batched count/state and avoid an
    # N+1; single-record endpoints fall back to a per-id query.
    event_count, latest_state = (
        summary if summary is not None else ctx.store.timelapse_analysis_summary(r.id)
    )
    return TimelapseInfo(
        id=r.id,
        camera_id=r.camera_id,
        name=r.name,
        state=r.state,
        mode=r.mode,
        interval_seconds=r.interval_seconds,
        output_fps=r.output_fps,
        frames_captured=r.frames_captured,
        created_ts=r.created_ts,
        start_ts=r.start_ts,
        end_ts=r.end_ts,
        size_bytes=r.size_bytes,
        width=r.width,
        height=r.height,
        has_video=bool(r.video_path),
        has_thumb=bool(r.thumb_path),
        smooth_state=r.smooth_state,
        has_smooth=bool(r.smooth_path),
        smooth_size_bytes=r.smooth_size_bytes,
        smooth_engine=r.smooth_engine,
        jpeg_quality=r.jpeg_quality,
        max_frames=r.max_frames,
        auto_smooth=r.auto_smooth,
        smooth_target_fps=r.smooth_target_fps,
        smooth_interpolate=r.smooth_interpolate,
        smooth_deflicker=r.smooth_deflicker,
        smooth_quality=r.smooth_quality,
        analysis_enabled=r.analysis_enabled,
        analysis_cadence_seconds=r.analysis_cadence_seconds,
        analysis_event_count=event_count,
        analysis_latest_state=latest_state,
        host=ctx.local_host,
        proxy_prefix="",
        source_host=r.source_host,
    )


@router.get("/timelapse-presets", response_model=list[dict[str, object]])
def list_timelapse_presets() -> list[dict[str, object]]:
    return printer_presets()


@router.get("/timelapse", response_model=list[TimelapseInfo])
async def list_timelapses(
    camera_id: str | None = None,
    limit: int = 100,
    scope: str = Query("all", pattern="^(all|local)$"),
    ctx: AppContext = Depends(get_context),
) -> list[TimelapseInfo]:
    """Timelapses across the fleet (default) or only this node's (``scope=local``).

    A capture started on a peer's camera runs on that peer (or on the storage
    node); without aggregation it would be invisible from the node you opened —
    and impossible to stop — so every list merges the peers' local lists."""
    summaries = ctx.store.timelapse_analysis_summaries()
    local = [
        _timelapse_info(ctx, r, summaries.get(r.id or 0, (0, "")))
        for r in ctx.timelapse.list(camera_id, limit)
    ]
    if scope == "local":
        return local
    remote_raw = await ctx.cluster.remote_timelapses(
        {"limit": limit, **({"camera_id": camera_id} if camera_id else {})}
    )
    remote: list[TimelapseInfo] = []
    for item in remote_raw:
        try:
            remote.append(TimelapseInfo.model_validate(item))
        except ValueError:
            continue  # a peer on an older version — skip rather than 500
    merged = local + remote
    merged.sort(key=lambda t: t.created_ts, reverse=True)
    return merged[:limit]


@router.post("/cameras/{camera_id:path}/timelapse/start", response_model=TimelapseInfo)
def start_timelapse(
    camera_id: str,
    req: TimelapseStartRequest | None = None,
    ctx: AppContext = Depends(get_context),
) -> TimelapseInfo:
    req = req or TimelapseStartRequest()
    analysis_enabled = (
        ctx.config.timelapse.analysis_enabled
        if req.analysis_enabled is None
        else req.analysis_enabled
    )
    if analysis_enabled and not ctx.analyzer.enabled and not ctx.capture.target():
        raise HTTPException(
            status_code=409,
            detail="Enable and configure Ollama on the Models page before printer analysis",
        )
    params = dict(
        name=req.name,
        interval_seconds=req.interval_seconds,
        output_fps=req.output_fps,
        duration_seconds=req.duration_seconds,
        jpeg_quality=req.jpeg_quality,
        max_frames=req.max_frames,
        auto_smooth=req.auto_smooth,
        smooth_target_fps=req.smooth_target_fps,
        smooth_interpolate=req.smooth_interpolate,
        smooth_deflicker=req.smooth_deflicker,
        smooth_engine=req.smooth_engine,
        smooth_quality=req.smooth_quality,
        analysis_enabled=req.analysis_enabled,
        analysis_cadence_seconds=req.analysis_cadence_seconds,
    )
    # Runs here, or on the storage node (which pulls this camera's stream).
    record = ctx.capture.start_timelapse(camera_id, params)
    if record is None:
        raise HTTPException(status_code=503, detail="camera unavailable")
    if isinstance(record, dict):
        return TimelapseInfo.model_validate(record)
    return _timelapse_info(ctx, record)


@router.get(
    "/timelapse/{tl_id}/analysis-events",
    response_model=list[TimelapseAnalysisEventInfo],
)
def list_timelapse_analysis_events(
    tl_id: int, ctx: AppContext = Depends(get_context)
) -> list[TimelapseAnalysisEventInfo]:
    if ctx.timelapse.get(tl_id) is None:
        raise HTTPException(status_code=404, detail="timelapse not found")
    return [
        TimelapseAnalysisEventInfo.model_validate(event, from_attributes=True)
        for event in ctx.store.list_timelapse_analysis_events(tl_id)
    ]


@router.get("/timelapse/{tl_id}", response_model=TimelapseInfo)
def get_timelapse(tl_id: int, ctx: AppContext = Depends(get_context)) -> TimelapseInfo:
    record = ctx.timelapse.get(tl_id)
    if record is None:
        raise HTTPException(status_code=404, detail="timelapse not found")
    return _timelapse_info(ctx, record)


@router.post("/timelapse/{tl_id}/stop", response_model=TimelapseInfo)
def stop_timelapse(tl_id: int, ctx: AppContext = Depends(get_context)) -> TimelapseInfo:
    record = ctx.timelapse.stop(tl_id)
    if record is None:
        raise HTTPException(status_code=404, detail="timelapse not found")
    return _timelapse_info(ctx, record)


@router.post("/timelapse/{tl_id}/encode", response_model=TimelapseInfo)
def encode_timelapse(tl_id: int, ctx: AppContext = Depends(get_context)) -> TimelapseInfo:
    """(Re)encode a stopped or interrupted timelapse from its stored frames."""
    record = ctx.timelapse.encode(tl_id)
    if record is None:
        raise HTTPException(status_code=404, detail="timelapse not found")
    return _timelapse_info(ctx, record)


@router.post("/timelapse/{tl_id}/smooth", response_model=TimelapseInfo)
def smooth_timelapse(
    tl_id: int,
    req: TimelapseSmoothRequest | None = None,
    ctx: AppContext = Depends(get_context),
) -> TimelapseInfo:
    """Post-process a timelapse into smooth motion (ffmpeg interpolation)."""
    from tailcam.timelapse.ffmpeg import ffmpeg_available

    if not ffmpeg_available():
        raise HTTPException(status_code=503, detail="ffmpeg not available")
    req = req or TimelapseSmoothRequest()
    record = ctx.timelapse.smooth(
        tl_id,
        target_fps=req.target_fps,
        interpolate=req.interpolate,
        deflicker=req.deflicker,
        engine=req.engine,
        quality=req.quality,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="timelapse not found or has no frames")
    return _timelapse_info(ctx, record)


@router.delete("/timelapse/{tl_id}", response_model=OkResponse)
def delete_timelapse(tl_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    if not ctx.timelapse.delete(tl_id):
        raise HTTPException(status_code=404, detail="timelapse not found")
    return OkResponse(detail="deleted")


def _postprocess_info(ctx: AppContext) -> PostprocessInfo:
    from tailcam.timelapse.ffmpeg import ffmpeg_source, ffmpeg_version
    from tailcam.timelapse.rife import rife_available

    tl = ctx.config.timelapse
    ff_source = ffmpeg_source()
    rife_ok = rife_available(tl.rife_path)
    engines = [
        EngineInfo(
            id="ffmpeg",
            label="ffmpeg (minterpolate)",
            available=ff_source != "missing",
            source=ff_source,
            version=ffmpeg_version(),
        ),
        EngineInfo(
            id="rife",
            label="RIFE (rife-ncnn-vulkan)",
            available=rife_ok,
            source="system" if rife_ok else "missing",
            version=None,
        ),
    ]
    return PostprocessInfo(
        available=any(e.available for e in engines),
        default_engine=tl.smooth_engine,
        default_target_fps=tl.smooth_target_fps,
        engines=engines,
    )


@router.get("/postprocess", response_model=PostprocessInfo)
def postprocess_info(ctx: AppContext = Depends(get_context)) -> PostprocessInfo:
    """Interpolation engines available for timelapse smoothing (status page)."""
    return _postprocess_info(ctx)


@router.post("/postprocess", response_model=PostprocessInfo)
def set_postprocess(
    settings: PostprocessSettings, ctx: AppContext = Depends(get_context)
) -> PostprocessInfo:
    """Set the default smoothing engine (persisted to config)."""
    if settings.default_engine is not None:
        if settings.default_engine not in ("ffmpeg", "rife"):
            raise HTTPException(status_code=400, detail="engine must be ffmpeg or rife")
        ctx.config.timelapse.smooth_engine = settings.default_engine
        ctx.config.save()
    return _postprocess_info(ctx)


# -- model training --------------------------------------------------------


def _dataset_info(ctx: AppContext, d) -> DatasetInfo:
    counts = ctx.store.dataset_label_counts(d.id)
    box_counts: dict[str, int] = {}
    annotated = 0
    if d.task == "detection":
        box_counts = ctx.store.dataset_annotation_label_counts(d.id)
        annotated = len(ctx.store.annotation_counts(d.id))
    return DatasetInfo(
        id=d.id,
        name=d.name,
        task=d.task,
        created_ts=d.created_ts,
        note=d.note,
        sample_count=sum(counts.values()),
        label_counts=counts,
        annotated_count=annotated,
        box_label_counts=box_counts,
    )


def _sample_info(s, ann_count: int = 0) -> SampleInfo:
    return SampleInfo(
        id=s.id,
        dataset_id=s.dataset_id,
        label=s.label,
        source=s.source,
        camera_id=s.camera_id,
        host=s.host,
        created_ts=s.created_ts,
        confidence=s.confidence,
        has_thumb=bool(s.thumb),
        annotation_count=ann_count,
    )


def _model_info(m) -> ModelInfo:
    try:
        classes = json.loads(m.classes_json) or []
    except (ValueError, TypeError):
        classes = []
    try:
        metrics = json.loads(m.metrics_json) or {}
    except (ValueError, TypeError):
        metrics = {}
    return ModelInfo(
        id=m.id,
        name=m.name,
        kind=m.kind,
        task=m.task,
        active=bool(m.active),
        base_model=m.base_model,
        classes=classes,
        metrics=metrics,
        created_ts=m.created_ts,
        has_artifact=bool(m.path),
    )


def _training_info(ctx: AppContext) -> TrainingInfo:
    from tailcam.training.engine import engine_info

    info = engine_info()
    tc = ctx.config.training
    return TrainingInfo(
        engine_available=info["available"],
        framework=info["framework"],
        version=info["version"],
        device=info["device"],
        collecting=ctx.training.is_collecting(),
        collect_enabled=tc.collect_enabled,
        collect_interval_seconds=tc.collect_interval_seconds,
        auto_label=tc.auto_label,
        active_dataset_id=tc.active_dataset_id,
        active_model_id=tc.active_model_id,
        classes=list(tc.classes),
        total_samples=ctx.store.total_sample_count(),
        dataset_count=len(ctx.store.list_datasets()),
        model_count=len(ctx.store.list_models()),
        collected_session=ctx.training.collected_this_session,
    )


@router.get("/training", response_model=TrainingInfo)
def training_info(ctx: AppContext = Depends(get_context)) -> TrainingInfo:
    """Training engine status + a summary for the Training page."""
    return _training_info(ctx)


@router.post("/training/collection", response_model=TrainingInfo)
def update_collection(
    upd: CollectionUpdate, ctx: AppContext = Depends(get_context)
) -> TrainingInfo:
    """Toggle/configure continuous dataset collection from the camera feeds."""
    tc = ctx.config.training
    if upd.interval_seconds is not None:
        tc.collect_interval_seconds = max(2.0, upd.interval_seconds)
    if upd.auto_label is not None:
        tc.auto_label = upd.auto_label
    if upd.active_dataset_id is not None:
        tc.active_dataset_id = upd.active_dataset_id
    if upd.enabled is not None:
        tc.collect_enabled = upd.enabled
        if upd.enabled:
            if not tc.active_dataset_id:
                ds = ctx.training.create_dataset("All cameras")
                tc.active_dataset_id = ds.id or 0
            ctx.training.start_collection()
        else:
            ctx.training.stop_collection()
    ctx.config.save()
    return _training_info(ctx)


@router.get("/datasets", response_model=list[DatasetInfo])
def list_datasets(ctx: AppContext = Depends(get_context)) -> list[DatasetInfo]:
    return [_dataset_info(ctx, d) for d in ctx.store.list_datasets()]


@router.post("/datasets", response_model=DatasetInfo)
def create_dataset(body: DatasetCreate, ctx: AppContext = Depends(get_context)) -> DatasetInfo:
    record = ctx.training.create_dataset(body.name, body.note, body.task)
    ctx.config.save()
    return _dataset_info(ctx, record)


@router.get("/datasets/{dataset_id}", response_model=DatasetInfo)
def get_dataset(dataset_id: int, ctx: AppContext = Depends(get_context)) -> DatasetInfo:
    d = ctx.store.get_dataset(dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return _dataset_info(ctx, d)


@router.delete("/datasets/{dataset_id}", response_model=OkResponse)
def delete_dataset(dataset_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    if not ctx.training.delete_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    ctx.config.save()
    return OkResponse(detail="deleted")


@router.post("/datasets/{dataset_id}/import-events", response_model=DatasetInfo)
def import_events(dataset_id: int, ctx: AppContext = Depends(get_context)) -> DatasetInfo:
    """Add existing motion-event snapshots to the dataset as labeled samples."""
    d = ctx.store.get_dataset(dataset_id)
    if d is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    ctx.training.import_from_events(dataset_id)
    return _dataset_info(ctx, ctx.store.get_dataset(dataset_id))


@router.get("/datasets/{dataset_id}/samples", response_model=list[SampleInfo])
def list_samples(
    dataset_id: int,
    label: str | None = None,
    limit: int = 200,
    offset: int = 0,
    ctx: AppContext = Depends(get_context),
) -> list[SampleInfo]:
    counts = ctx.store.annotation_counts(dataset_id)
    return [
        _sample_info(s, counts.get(s.id or 0, 0))
        for s in ctx.store.list_samples(dataset_id, label, limit, offset)
    ]


@router.patch("/samples/{sample_id}", response_model=SampleInfo)
def relabel_sample(
    sample_id: int, body: SampleRelabel, ctx: AppContext = Depends(get_context)
) -> SampleInfo:
    s = ctx.store.get_sample(sample_id)
    if s is None:
        raise HTTPException(status_code=404, detail="sample not found")
    ctx.store.set_sample_label(sample_id, body.label)
    updated = ctx.store.get_sample(sample_id)
    return _sample_info(updated, len(ctx.store.list_annotations(sample_id)))


@router.delete("/samples/{sample_id}", response_model=OkResponse)
def delete_sample(sample_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    if not ctx.training.delete_sample(sample_id):
        raise HTTPException(status_code=404, detail="sample not found")
    return OkResponse(detail="deleted")


def _annotation_box(a) -> AnnotationBox:
    return AnnotationBox(label=a.label, cx=a.cx, cy=a.cy, w=a.w, h=a.h)


@router.get("/samples/{sample_id}/annotations", response_model=SampleAnnotations)
def get_sample_annotations(
    sample_id: int, ctx: AppContext = Depends(get_context)
) -> SampleAnnotations:
    if ctx.store.get_sample(sample_id) is None:
        raise HTTPException(status_code=404, detail="sample not found")
    boxes = [_annotation_box(a) for a in ctx.store.list_annotations(sample_id)]
    return SampleAnnotations(sample_id=sample_id, boxes=boxes)


@router.put("/samples/{sample_id}/annotations", response_model=SampleAnnotations)
def set_sample_annotations(
    sample_id: int,
    body: SampleAnnotationsUpdate,
    ctx: AppContext = Depends(get_context),
) -> SampleAnnotations:
    """Replace a detection sample's bounding boxes (the annotation editor sends
    the full set on save)."""
    stored = ctx.training.set_annotations(
        sample_id, [b.model_dump() for b in body.boxes]
    )
    if stored is None:
        raise HTTPException(status_code=404, detail="sample not found")
    return SampleAnnotations(
        sample_id=sample_id, boxes=[_annotation_box(a) for a in stored]
    )


@router.get("/models", response_model=list[ModelInfo])
def list_models(ctx: AppContext = Depends(get_context)) -> list[ModelInfo]:
    return [_model_info(m) for m in ctx.store.list_models()]


@router.post("/models", response_model=ModelInfo)
def register_model(body: ModelRegister, ctx: AppContext = Depends(get_context)) -> ModelInfo:
    """Register a bring-your-own model file (.pt) by path."""
    record = ctx.training.register_byo(body.name, body.path, body.task)
    if record is None:
        raise HTTPException(status_code=400, detail="model file not found at that path")
    return _model_info(record)


@router.post("/models/{model_id}/activate", response_model=ModelInfo)
def activate_model(model_id: int, ctx: AppContext = Depends(get_context)) -> ModelInfo:
    m = ctx.store.get_model(model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="model not found")
    ctx.training.activate_model(model_id)
    ctx.config.save()
    return _model_info(ctx.store.get_model(model_id))


@router.post("/models/deactivate", response_model=OkResponse)
def deactivate_model(ctx: AppContext = Depends(get_context)) -> OkResponse:
    """Use the default analyzer (Ollama) instead of a trained/BYO model."""
    ctx.training.activate_model(None)
    ctx.config.save()
    return OkResponse(detail="using default analyzer")


@router.delete("/models/{model_id}", response_model=OkResponse)
def delete_model(model_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    if not ctx.training.delete_model(model_id):
        raise HTTPException(status_code=400, detail="cannot delete (not found or base model)")
    ctx.config.save()
    return OkResponse(detail="deleted")


def _run_info(r) -> TrainingRunInfo:
    try:
        metrics = json.loads(r.metrics_json) or {}
    except (ValueError, TypeError):
        metrics = {}
    return TrainingRunInfo(
        id=r.id,
        dataset_id=r.dataset_id,
        model_id=r.model_id,
        base_model=r.base_model,
        status=r.status,
        epochs=r.epochs,
        epoch=r.epoch,
        metrics=metrics,
        log=r.log,
        created_ts=r.created_ts,
        started_ts=r.started_ts,
        ended_ts=r.ended_ts,
    )


@router.post("/training/runs", response_model=TrainingRunInfo)
def start_run(body: TrainRequest, ctx: AppContext = Depends(get_context)) -> TrainingRunInfo:
    """Fine-tune a model on a dataset (needs the training engine installed)."""
    from tailcam.training.engine import engine_available

    if not engine_available():
        raise HTTPException(status_code=503, detail="training engine not installed")
    try:
        run = ctx.training.train(body.dataset_id, body.base_model, body.epochs, body.image_size)
    except RuntimeError as exc:  # a run is already in progress
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return _run_info(run)


@router.get("/training/runs", response_model=list[TrainingRunInfo])
def list_runs(ctx: AppContext = Depends(get_context)) -> list[TrainingRunInfo]:
    return [_run_info(r) for r in ctx.store.list_runs()]


@router.get("/training/runs/{run_id}", response_model=TrainingRunInfo)
def get_run(run_id: int, ctx: AppContext = Depends(get_context)) -> TrainingRunInfo:
    r = ctx.store.get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_info(r)


@router.post("/training/runs/{run_id}/stop", response_model=TrainingRunInfo)
def stop_run(run_id: int, ctx: AppContext = Depends(get_context)) -> TrainingRunInfo:
    r = ctx.store.get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="run not found")
    ctx.training.stop_run(run_id)
    return _run_info(ctx.store.get_run(run_id))


@router.get("/system", response_model=SystemInfo)
def system_info(ctx: AppContext = Depends(get_context)) -> SystemInfo:
    from tailcam import hostinfo

    prof = hostinfo.profile()
    status = ctx.tailscale.status()
    port = ctx.config.server.port
    return SystemInfo(
        version=__version__,
        host=ctx.local_host,
        tailscale_installed=status.installed,
        tailscale_running=status.running,
        access_url=ctx.tailscale.access_url(port, ctx.served, ctx.config.tailscale.serve_port),
        local_url=f"http://localhost:{port}/",
        media_bytes=ctx.gallery.total_bytes() + ctx.store.total_timelapse_bytes(),
        hidden_count=len(ctx.config.cameras.hidden),
        host_model=prof.model,
        ram_gb=prof.total_ram_gb,
        cpu_count=prof.cpu_count,
        low_power=prof.low_power,
    )


def _streaming_defaults(ctx: AppContext) -> StreamingDefaults:
    st = ctx.config.stream
    return StreamingDefaults(fps=st.default_fps, quality=st.jpeg_quality, max_width=st.max_width)


@router.get("/streaming", response_model=StreamingDefaults)
def streaming_defaults(ctx: AppContext = Depends(get_context)) -> StreamingDefaults:
    """Global stream defaults every camera inherits (fps, JPEG quality, max width)."""
    return _streaming_defaults(ctx)


@router.post("/streaming", response_model=StreamingDefaults)
def update_streaming_defaults(
    update: StreamingDefaultsUpdate, ctx: AppContext = Depends(get_context)
) -> StreamingDefaults:
    st = ctx.config.stream
    if update.fps is not None:
        st.default_fps = update.fps
    if update.quality is not None:
        st.jpeg_quality = update.quality
    if update.max_width is not None:
        st.max_width = update.max_width
    ctx.config.save()
    return _streaming_defaults(ctx)


@router.post("/system/reload", response_model=list[CameraInfo])
async def system_reload(ctx: AppContext = Depends(get_context)) -> list[CameraInfo]:
    """Re-scan devices and restart all local capture workers (no process restart)."""
    import anyio

    # Each restart joins a capture thread (up to 5 s): never on the event loop.
    await anyio.to_thread.run_sync(_restart_all_local, ctx)
    await ctx.cluster.refresh(force=True)
    return await _aggregate_cameras(ctx, "all")


def _is_ollama(ctx: AppContext) -> bool:
    """Whether the active analyzer is the built-in Ollama provider (the only one
    that supports the model pull/list/load endpoints)."""
    return ctx.config.ai.provider == "ollama" and hasattr(ctx.analyzer, "health")


async def _ai_info(ctx: AppContext) -> AIInfo:
    import anyio

    if _is_ollama(ctx):
        reachable, model = await anyio.to_thread.run_sync(ctx.analyzer.health)
    else:
        reachable, model = False, None
    pipeline = await anyio.to_thread.run_sync(ctx.inference.describe)
    ai = ctx.config.ai
    return AIInfo(
        enabled=ai.enabled,
        reachable=reachable,
        model=ai.model,
        model_present=model is not None,
        base_url=ai.base_url,
        provider=ai.provider,
        pipeline=PipelineInfo(**pipeline),
    )


@router.get("/ai", response_model=AIInfo)
async def ai_info(ctx: AppContext = Depends(get_context)) -> AIInfo:
    """AI analyzer status for the dashboard (Ollama reachable? model present?)."""
    return await _ai_info(ctx)


@router.post("/ai", response_model=AIInfo)
async def update_ai(update: AIUpdate, ctx: AppContext = Depends(get_context)) -> AIInfo:
    """Enable/disable AI motion analysis and set the model/Ollama URL (persisted).

    The analyzer reads ``config.ai`` live, so the change takes effect immediately
    for in-flight motion workers — no restart."""
    ai = ctx.config.ai
    if update.enabled is not None:
        ai.enabled = update.enabled
    if update.model is not None and update.model.strip():
        ai.model = update.model.strip()
    if update.base_url is not None and update.base_url.strip():
        ai.base_url = update.base_url.strip().rstrip("/")
    if update.provider is not None and update.provider.strip():
        ai.provider = update.provider.strip()  # takes effect on restart
    ctx.config.save()
    return await _ai_info(ctx)


async def _ollama_models_info(ctx: AppContext) -> OllamaModelsInfo:
    import anyio

    if not _is_ollama(ctx):
        return OllamaModelsInfo(
            reachable=False, base_url=ctx.config.ai.base_url, active_model=ctx.config.ai.model
        )
    reachable, installed = await anyio.to_thread.run_sync(ctx.analyzer.installed_models)
    return OllamaModelsInfo(
        reachable=reachable,
        base_url=ctx.config.ai.base_url,
        active_model=ctx.config.ai.model,
        installed=installed,
    )


@router.post("/ai/test", response_model=AITestResult)
async def ai_test(body: AITestRequest, ctx: AppContext = Depends(get_context)) -> AITestResult:
    """Analyze one live frame from a camera through the SAME pipeline motion
    events use — so users can validate their setup without waiting for motion."""
    import anyio

    buffer = ctx.manager.get_buffer(body.camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="camera not found")

    def _run() -> AITestResult:
        frame = buffer.await_latest(-1, 3.0)
        if frame is None:
            return AITestResult(ok=False, error="no frame available from this camera")
        pipeline = ctx.inference.describe()
        if pipeline["mode"] == "off":
            return AITestResult(
                ok=False,
                error=pipeline["error"] or "AI analysis is off — enable it or activate a model",
            )
        result = ctx.inference.analyze(frame.image)
        if result is None:
            return AITestResult(
                ok=False,
                engine=pipeline["mode"],
                error="analysis returned nothing (is the model reachable and pulled?)",
            )
        return AITestResult(
            ok=True,
            engine=pipeline["mode"],
            label=result.label,
            confidence=result.confidence,
            description=result.description,
        )

    return await anyio.to_thread.run_sync(_run)


@router.get("/ai/models", response_model=OllamaModelsInfo)
async def ai_models(ctx: AppContext = Depends(get_context)) -> OllamaModelsInfo:
    """List models installed in the configured Ollama backend."""
    return await _ollama_models_info(ctx)


def _pull_status(state) -> AIPullStatus:
    return AIPullStatus(
        model=state.model, active=state.active, status=state.status,
        completed=state.completed, total=state.total, percent=state.percent,
        detail=state.detail, error=state.error,
    )


@router.post("/ai/pull", response_model=AIPullStatus)
async def ai_pull(
    body: AIModelRequest, ctx: AppContext = Depends(get_context)
) -> AIPullStatus:
    """Start downloading a model into Ollama (runs in the background; poll
    GET /ai/pull for progress). Large vision models can take several minutes."""
    import anyio

    if not _is_ollama(ctx):
        raise HTTPException(status_code=400, detail="Active AI provider is not Ollama")
    reachable, _ = await anyio.to_thread.run_sync(ctx.analyzer.installed_models)
    if not reachable:
        raise HTTPException(status_code=502, detail="Ollama is not reachable")
    return _pull_status(ctx.pulls.start(body.model))


@router.get("/ai/pull", response_model=AIPullStatus)
async def ai_pull_status(ctx: AppContext = Depends(get_context)) -> AIPullStatus:
    """Current model-download progress (for the UI's progress bar)."""
    return _pull_status(ctx.pulls.status())


@router.post("/ai/load", response_model=AIInfo)
async def ai_load(body: AIModelRequest, ctx: AppContext = Depends(get_context)) -> AIInfo:
    """Warm a model into Ollama's memory ('start' it) for fast first inference."""
    import anyio

    if not _is_ollama(ctx):
        raise HTTPException(status_code=400, detail="Active AI provider is not Ollama")
    ok = await anyio.to_thread.run_sync(ctx.analyzer.load, body.model)
    if not ok:
        raise HTTPException(
            status_code=502, detail="ollama load failed (model present and reachable?)"
        )
    return await _ai_info(ctx)


def _notifications_info(ctx: AppContext) -> NotificationsInfo:
    from dataclasses import asdict

    return NotificationsInfo(
        **asdict(ctx.config.notifications),
        channels=ctx.notifications.channels_configured(),
    )


@router.get("/notifications", response_model=NotificationsInfo)
def notifications_info(ctx: AppContext = Depends(get_context)) -> NotificationsInfo:
    """Current notification settings + which channels are configured."""
    return _notifications_info(ctx)


@router.post("/notifications", response_model=NotificationsInfo)
def update_notifications(
    body: NotificationsUpdate, ctx: AppContext = Depends(get_context)
) -> NotificationsInfo:
    """Update notification channels, triggers, and filters (persisted, live)."""
    cfg = ctx.config.notifications
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(cfg, key, value)
    ctx.config.save()
    return _notifications_info(ctx)


@router.post("/notifications/test", response_model=OkResponse)
async def test_notification(ctx: AppContext = Depends(get_context)) -> OkResponse:
    """Send a test notification to every configured channel."""
    import anyio

    channels = ctx.notifications.channels_configured()
    if not channels:
        raise HTTPException(
            status_code=400, detail="No channels configured — add Discord, Telegram, or a webhook."
        )
    result = await anyio.to_thread.run_sync(ctx.notifications.send_test)
    failed = [name for name, ok in result.get("results", {}).items() if not ok]
    if failed:
        raise HTTPException(status_code=502, detail=f"Test failed for: {', '.join(failed)}")
    return OkResponse(detail=f"Test sent to {', '.join(channels)}")


@router.get("/plugins", response_model=PluginsInfo)
def plugins_info(ctx: AppContext = Depends(get_context)) -> PluginsInfo:
    """Installed plugins plus the AI providers and notification channels they
    contribute. Plugins are added via pip (entry points) or the drop-in folder."""
    from dataclasses import asdict

    reg = ctx.plugins
    return PluginsInfo(
        plugins=[PluginEntry(**asdict(info)) for info in reg.plugin_infos()],
        analyzer_providers=[
            ProviderEntry(id=p.id, name=p.name, description=getattr(p, "description", ""))
            for p in reg.analyzer_providers()
        ],
        notification_channels=[
            ChannelEntry(id=c.id, name=c.name) for c in reg.notification_channels()
        ],
        active_provider=ctx.config.ai.provider,
        errors=reg.errors,
    )


def _mcp_info(ctx: AppContext) -> McpInfo:
    from tailcam.integrations.base import public_base_url
    from tailcam.mcp import (
        PROTOCOL_VERSION,
        RECOMMENDED_TOOLS,
        STATELESS,
        STDIO_ARGS,
        STDIO_COMMAND,
        SUPPORTED_PROTOCOL_VERSIONS,
    )
    from tailcam.mcp.tools import tool_count

    cfg = ctx.config.mcp
    port = ctx.config.server.port
    # The tailnet MCP URL only exists when Tailscale Serve fronts the node with
    # HTTPS + identity headers; a plain http:// base can't authenticate callers.
    # public_base_url() is the single source of truth for the served/HTTPS/port
    # rules (reused instead of re-deriving them here).
    base = public_base_url(ctx)
    http_tailnet = f"{base}/mcp" if base.startswith("https://") else ""
    return McpInfo(
        enabled=cfg.enabled,
        http_enabled=cfg.http_enabled,
        http_live=cfg.enabled and cfg.http_enabled,
        tools_count=tool_count(),
        stdio_command=STDIO_COMMAND,
        stdio_args=list(STDIO_ARGS),
        recommended_tools=list(RECOMMENDED_TOOLS),
        tailcam_url=f"http://127.0.0.1:{port}",
        http_url_tailnet=http_tailnet,
        http_url_local=f"http://localhost:{port}/mcp",
        tailscale_running=ctx.tailscale.status().running,
        protocol_version=PROTOCOL_VERSION,
        supported_protocol_versions=list(SUPPORTED_PROTOCOL_VERSIONS),
        stateless=STATELESS,
    )


@router.get("/mcp", response_model=McpInfo)
def mcp_info(ctx: AppContext = Depends(get_context)) -> McpInfo:
    """MCP server status + the URLs/commands agents need to connect."""
    return _mcp_info(ctx)


@router.post("/mcp", response_model=McpInfo)
def update_mcp(update: McpUpdate, ctx: AppContext = Depends(get_context)) -> McpInfo:
    """Toggle the MCP server / its HTTP endpoint (persisted, effective
    immediately — the /mcp route checks config per request)."""
    cfg = ctx.config.mcp
    if update.enabled is not None:
        cfg.enabled = update.enabled
    if update.http_enabled is not None:
        cfg.http_enabled = update.http_enabled
    ctx.config.save()
    return _mcp_info(ctx)


def _market_info(ctx: AppContext, *, force: bool = False) -> PluginsMarketInfo:
    catalog, error = ctx.market.catalog(force=force)
    installed = ctx.market.installed()
    installed_by_market = {p.market_id: p for p in installed if p.market_id}
    loaded = set(ctx.plugins.loaded_dropins)
    market_entries = []
    for p in catalog:
        inst = installed_by_market.get(p.id)
        market_entries.append(
            MarketPluginEntry(
                id=p.id, name=p.name, version=p.version, description=p.description,
                author=p.author, kinds=p.kinds, homepage=p.homepage,
                settings_example=p.settings_example,
                installed=inst is not None,
                installed_version=inst.version if inst else "",
                update_available=bool(inst and inst.update_available),
            )
        )
    return PluginsMarketInfo(
        registry_url=ctx.config.plugins.registry_url,
        error=error,
        market=market_entries,
        installed=[
            InstalledPluginEntry(
                id=p.id, file=p.file, version=p.version, source=p.source,
                market_id=p.market_id, enabled=p.enabled, loaded=p.id in loaded,
                update_available=p.update_available,
            )
            for p in installed
        ],
        load_errors=ctx.plugins.errors,
    )


@router.get("/plugins/market", response_model=PluginsMarketInfo)
def plugins_market(
    refresh: bool = False, ctx: AppContext = Depends(get_context)
) -> PluginsMarketInfo:
    """The curated plugin marketplace + this node's installed drop-ins."""
    return _market_info(ctx, force=refresh)


@router.post("/plugins/market/install", response_model=PluginsMarketInfo)
def plugins_market_install(
    body: PluginInstallRequest, ctx: AppContext = Depends(get_context)
) -> PluginsMarketInfo:
    """Install a plugin from the registry (sha256-verified) and hot-load it."""
    from tailcam.plugins.market import MarketError

    try:
        ctx.market.install(body.id.strip())
    except MarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ctx.reload_plugins()
    return _market_info(ctx)


@router.delete("/plugins/installed/{stem}", response_model=PluginsMarketInfo)
def plugins_uninstall(stem: str, ctx: AppContext = Depends(get_context)) -> PluginsMarketInfo:
    """Remove a drop-in plugin file and unload it."""
    from tailcam.plugins.market import MarketError

    try:
        removed = ctx.market.uninstall(stem)
    except MarketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="plugin not found")
    if stem in ctx.config.plugins.disabled:
        ctx.config.plugins.disabled.remove(stem)
        ctx.config.save()
    ctx.reload_plugins()
    return _market_info(ctx)


@router.post("/plugins/installed/{stem}/toggle", response_model=PluginsMarketInfo)
def plugins_toggle(
    stem: str, body: PluginToggleRequest, ctx: AppContext = Depends(get_context)
) -> PluginsMarketInfo:
    """Enable/disable an installed plugin (persisted) and reload the registry."""
    disabled = ctx.config.plugins.disabled
    if body.enabled and stem in disabled:
        disabled.remove(stem)
    elif not body.enabled and stem not in disabled:
        disabled.append(stem)
    ctx.config.save()
    ctx.reload_plugins()
    return _market_info(ctx)


@router.post("/plugins/reload", response_model=PluginsMarketInfo)
def plugins_reload(ctx: AppContext = Depends(get_context)) -> PluginsMarketInfo:
    """Re-scan the drop-in folder (after manual file edits)."""
    ctx.reload_plugins()
    return _market_info(ctx)


def _homeassistant_status(ctx: AppContext) -> HomeAssistantStatus:
    from tailcam.integrations.base import public_base_url
    from tailcam.integrations.homeassistant import MqttPublisher, camera_entries, camera_yaml

    cfg = ctx.config.homeassistant
    entries = camera_entries(ctx)
    return HomeAssistantStatus(
        enabled=cfg.enabled,
        mqtt_available=MqttPublisher.available(),
        mqtt_configured=bool(cfg.mqtt_host.strip()),
        mqtt_connected=bool(ctx.ha_mqtt is not None and ctx.ha_mqtt.connected),
        mqtt_host=cfg.mqtt_host,
        mqtt_port=cfg.mqtt_port,
        mqtt_username=cfg.mqtt_username,
        mqtt_tls=cfg.mqtt_tls,
        discovery_prefix=cfg.discovery_prefix,
        node_id=cfg.node_id,
        publish_motion=cfg.publish_motion,
        publish_status=cfg.publish_status,
        base_url=public_base_url(ctx),
        cameras=[HACameraEntry(**e) for e in entries],
        yaml=camera_yaml(ctx, entries),
    )


@router.get("/integrations", response_model=IntegrationsInfo)
def integrations_info(ctx: AppContext = Depends(get_context)) -> IntegrationsInfo:
    """Apple HomeKit + Home Assistant integration status, pairing info, and the
    ready-to-paste Home Assistant camera config."""
    return IntegrationsInfo(
        homekit=HomeKitStatus(**ctx.homekit.status()),
        homeassistant=_homeassistant_status(ctx),
    )


@router.post("/integrations/homekit", response_model=HomeKitStatus)
def update_homekit(update: HomeKitUpdate, ctx: AppContext = Depends(get_context)) -> HomeKitStatus:
    """Enable/configure the Apple HomeKit bridge (persisted). Re-(starts) the
    bridge so changes (cameras, name, pin) take effect immediately."""
    from tailcam.integrations.homekit import generate_pin

    cfg = ctx.config.homekit
    if update.regenerate_pin:
        cfg.pin = generate_pin()
    if update.bridge_name is not None and update.bridge_name.strip():
        cfg.bridge_name = update.bridge_name.strip()
    if update.port is not None:
        cfg.port = update.port
    if update.cameras is not None:
        cfg.cameras = update.cameras
    if update.enabled is not None:
        cfg.enabled = update.enabled
    ctx.config.save()
    if cfg.enabled:
        ctx.homekit.restart()
    else:
        ctx.homekit.stop()
    return HomeKitStatus(**ctx.homekit.status())


@router.post("/integrations/homekit/reset", response_model=HomeKitStatus)
def reset_homekit(ctx: AppContext = Depends(get_context)) -> HomeKitStatus:
    """Forget all paired controllers so HomeKit can be paired from scratch."""
    ctx.homekit.reset_pairing()
    return HomeKitStatus(**ctx.homekit.status())


@router.post("/integrations/homeassistant", response_model=HomeAssistantStatus)
def update_homeassistant(
    update: HomeAssistantUpdate, ctx: AppContext = Depends(get_context)
) -> HomeAssistantStatus:
    """Configure the Home Assistant integration (persisted). (Re)connects the
    optional MQTT discovery publisher."""

    cfg = ctx.config.homeassistant
    for field_name in (
        "mqtt_host", "mqtt_username", "mqtt_password", "discovery_prefix", "node_id",
    ):
        value = getattr(update, field_name)
        if value is not None:
            setattr(cfg, field_name, value.strip())
    if update.mqtt_port is not None:
        cfg.mqtt_port = update.mqtt_port
    if update.mqtt_tls is not None:
        cfg.mqtt_tls = update.mqtt_tls
    if update.publish_motion is not None:
        cfg.publish_motion = update.publish_motion
    if update.publish_status is not None:
        cfg.publish_status = update.publish_status
    if update.enabled is not None:
        cfg.enabled = update.enabled
    ctx.config.save()
    # Restart MQTT to apply (no-ops when disabled / no broker / no paho).
    ctx.apply_homeassistant_config()
    return _homeassistant_status(ctx)


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".tailcam_write_test"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


async def _storage_nodes(ctx: AppContext, local: StorageInfo) -> list[StorageNodeInfo]:
    """This device plus every online peer, with their free disk (for the
    storage-node picker)."""
    nodes = [
        StorageNodeInfo(
            node_key="local", host=ctx.local_host, online=True, media_dir=local.media_dir,
            disk_total=local.disk_total, disk_free=local.disk_free, version=__version__,
        )
    ]
    peers = {p.key: p for p in await ctx.cluster.peers()}
    remote = await ctx.cluster.remote_json("/api/storage")
    for key, peer in peers.items():
        data = remote.get(key) or {}
        nodes.append(
            StorageNodeInfo(
                node_key=key, host=peer.host, online=peer.online and bool(data),
                media_dir=str(data.get("media_dir", "")),
                disk_total=int(data.get("disk_total", 0) or 0),
                disk_free=int(data.get("disk_free", 0) or 0),
                version=peer.version,
            )
        )
    return nodes


def _storage_info(ctx: AppContext) -> StorageInfo:
    import os
    import shutil

    from tailcam import hostinfo, paths

    resolved = paths.media_dir()
    custom = ctx.config.storage.media_dir.strip()
    # Nearest existing ancestor: used for disk stats and a NON-mutating
    # writability check (a GET must not create directories or write probes —
    # that would materialize an unmounted mountpoint on the root filesystem).
    probe = resolved
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        du = shutil.disk_usage(probe)
        total, free, used = du.total, du.free, du.used
    except OSError:
        total = free = used = 0
    writable = os.access(resolved if resolved.exists() else probe, os.W_OK)
    m, r = ctx.config.motion, ctx.config.retention
    route = ctx.capture.status()
    return StorageInfo(
        media_dir=str(resolved),
        custom_dir=custom,
        is_default=(custom == ""),
        writable=writable,
        disk_total=total,
        disk_free=free,
        disk_used=used,
        media_bytes=ctx.gallery.total_bytes(),
        media_count=ctx.store.count_media(),
        timelapse_bytes=ctx.store.total_timelapse_bytes(),
        node=str(route["node"]),
        node_online=bool(route["node_online"]),
        node_error=str(route["error"]),
        low_power_host=hostinfo.is_low_power(),
        auto_record=m.auto_record,
        record_tail_seconds=m.record_tail_seconds,
        retention_enabled=r.enabled,
        max_gb=r.max_gb,
        max_age_days=r.max_age_days,
    )


@router.get("/storage", response_model=StorageInfo)
async def storage_info(
    scope: str = Query("all", pattern="^(all|local)$"),
    ctx: AppContext = Depends(get_context),
) -> StorageInfo:
    """Where recordings/snapshots are saved, disk usage, motion auto-record, the
    retention budget, and (``scope=all``) every fleet node's disk for the
    storage-node picker."""
    info = _storage_info(ctx)
    if scope == "all":
        info.nodes = await _storage_nodes(ctx, info)
    return info


@router.post("/storage", response_model=StorageInfo)
async def update_storage(
    update: StorageUpdate, ctx: AppContext = Depends(get_context)
) -> StorageInfo:
    """Set the save location (validated for writability), the storage node,
    motion auto-record, and retention. A blank media_dir reverts to the default
    app data location; a blank node saves on this device."""
    from tailcam import paths

    if update.node is not None:
        node = update.node.strip()
        if node and node.lower() in ("local", ctx.local_host.lower()):
            node = ""
        if node and ctx.resolve_node_base(node) is None:
            await ctx.cluster.refresh(force=True)
            if ctx.resolve_node_base(node) is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{node}' is not a TailCam node visible on the tailnet",
                )
        ctx.config.storage.node = node
    if update.media_dir is not None:
        new = update.media_dir.strip()
        if new:
            target = Path(new).expanduser()
            if not target.is_absolute():
                # A relative path would be re-resolved against whatever cwd the
                # service happens to have on the next boot (e.g. / under systemd).
                raise HTTPException(
                    status_code=400,
                    detail=f"'{new}' must be an absolute path (e.g. /mnt/usb/tailcam)",
                )
            if not _writable(target):
                raise HTTPException(
                    status_code=400, detail=f"Cannot create or write to '{new}'"
                )
            ctx.config.storage.media_dir = str(target)
        else:
            ctx.config.storage.media_dir = ""
        paths.set_media_override(ctx.config.storage.media_dir)
    if update.auto_record is not None:
        ctx.config.motion.auto_record = update.auto_record
    if update.record_tail_seconds is not None:
        ctx.config.motion.record_tail_seconds = max(0.0, update.record_tail_seconds)
    if update.retention_enabled is not None:
        ctx.config.retention.enabled = update.retention_enabled
    if update.max_gb is not None:
        ctx.config.retention.max_gb = max(0.1, update.max_gb)
    if update.max_age_days is not None:
        ctx.config.retention.max_age_days = max(1, update.max_age_days)
    ctx.config.save()
    info = _storage_info(ctx)
    info.nodes = await _storage_nodes(ctx, info)
    return info


def _fs_roots() -> list[FsEntry]:
    """Sensible starting points: home, the media default, and mounted drives."""
    import os
    import sys

    from tailcam import paths

    roots: list[Path] = [Path.home(), paths.default_media_dir()]
    if sys.platform == "win32":
        import string

        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                roots.append(drive)
    else:
        roots.append(Path("/"))
        for base in ("/media", "/mnt", "/Volumes", "/srv", "/run/media"):
            b = Path(base)
            if b.is_dir():
                try:
                    roots.extend(p for p in sorted(b.iterdir()) if p.is_dir())
                except OSError:
                    continue
                if base == "/run/media":  # /run/media/<user>/<drive>
                    for user_dir in list(roots):
                        if str(user_dir).startswith("/run/media/") and user_dir.is_dir():
                            try:
                                roots.extend(
                                    p for p in sorted(user_dir.iterdir()) if p.is_dir()
                                )
                            except OSError:
                                pass
    out: list[FsEntry] = []
    seen: set[str] = set()
    for r in roots:
        try:
            resolved = str(r.resolve())
        except OSError:
            continue
        if resolved in seen or not r.exists():
            continue
        seen.add(resolved)
        out.append(FsEntry(name=r.name or resolved, path=resolved, writable=os.access(r, os.W_OK)))
    return out


@router.get("/fs/browse", response_model=FsBrowseInfo)
def fs_browse(
    path: str = Query("", max_length=4096),
    show_hidden: bool = False,
) -> FsBrowseInfo:
    """List the sub-folders of ``path`` on THIS node (folders only, never file
    contents) so the save location can be picked without typing a path from
    memory — including on a remote node, through the proxy. Blank ``path``
    returns starting points (home, mounted drives)."""
    import os
    import shutil

    roots = _fs_roots()
    if not path.strip():
        return FsBrowseInfo(path="", roots=roots)
    target = Path(path).expanduser()
    if not target.is_absolute():
        return FsBrowseInfo(path=path, exists=False, roots=roots, error="path must be absolute")
    try:
        target = target.resolve()
    except OSError as exc:
        return FsBrowseInfo(path=path, exists=False, roots=roots, error=str(exc))
    parent = str(target.parent) if target.parent != target else None
    if not target.exists():
        return FsBrowseInfo(path=str(target), parent=parent, exists=False, roots=roots,
                            error="folder does not exist (it will be created when set)")
    if not target.is_dir():
        return FsBrowseInfo(path=str(target), parent=parent, exists=False, roots=roots,
                            error="not a folder")
    entries: list[FsEntry] = []
    try:
        for child in sorted(target.iterdir(), key=lambda c: c.name.lower()):
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                if not child.is_dir():
                    continue
                entries.append(
                    FsEntry(name=child.name, path=str(child), writable=os.access(child, os.W_OK))
                )
            except OSError:
                continue
    except PermissionError:
        return FsBrowseInfo(path=str(target), parent=parent, roots=roots, error="permission denied")
    try:
        du = shutil.disk_usage(target)
        total, free = du.total, du.free
    except OSError:
        total = free = 0
    return FsBrowseInfo(
        path=str(target), parent=parent, exists=True, writable=os.access(target, os.W_OK),
        disk_total=total, disk_free=free, entries=entries, roots=roots,
    )


@router.get("/update", response_model=UpdateInfo)
async def update_info(ctx: AppContext = Depends(get_context)) -> UpdateInfo:
    """Cached check for a newer TailCam release (for the dashboard banner)."""
    import anyio

    from tailcam import update as upd

    current, latest, available = await anyio.to_thread.run_sync(upd.update_available)
    return UpdateInfo(current=current, latest=latest, available=available)
