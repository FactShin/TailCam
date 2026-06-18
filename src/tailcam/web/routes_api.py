"""REST API endpoints under /api."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from tailcam import __version__
from tailcam.camera.manager import ManagedCamera
from tailcam.timelapse.presets import printer_presets
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.schemas import (
    AIInfo,
    AIModelRequest,
    AIUpdate,
    AnnotationBox,
    CameraInfo,
    CameraSettingsUpdate,
    CollectionUpdate,
    DatasetCreate,
    DatasetInfo,
    DetectionBox,
    DetectionResult,
    EngineInfo,
    HostInfo,
    MediaCreatedResponse,
    MediaInfo,
    ModelInfo,
    ModelRegister,
    MotionEventInfo,
    OkResponse,
    OllamaModelsInfo,
    PostprocessInfo,
    PostprocessSettings,
    SampleAnnotations,
    SampleAnnotationsUpdate,
    SampleInfo,
    SampleRelabel,
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
        recording=ctx.recorder.is_recording(cam.descriptor.id),
        motion_enabled=ctx.motion_enabled(cam.descriptor.id),
        properties=cam.properties.to_dict(),
        transform=TransformModel(
            rotation=cam.transform.rotation,
            flip_h=cam.transform.flip_h,
            flip_v=cam.transform.flip_v,
        ),
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
    ctx.manager.discover()
    ctx.manager.start_all()
    if scope != "local":
        await ctx.cluster.refresh(force=True)
    return await _aggregate_cameras(ctx, scope)


@router.post("/cameras/restore-hidden", response_model=list[CameraInfo])
async def restore_hidden(ctx: AppContext = Depends(get_context)) -> list[CameraInfo]:
    """Un-hide every deleted/forgotten camera and re-scan."""
    if ctx.config.cameras.hidden:
        ctx.config.cameras.hidden.clear()
        ctx.config.save()
    ctx.manager.discover()
    ctx.manager.start_all()
    return await _aggregate_cameras(ctx, "all")


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
    started = ctx.recorder.start(camera_id, fps=ctx.config.stream.default_fps)
    if not started:
        raise HTTPException(status_code=409, detail="already recording or camera unavailable")
    return OkResponse(detail="recording started")


@router.post("/cameras/{camera_id:path}/recording/stop", response_model=MediaCreatedResponse)
def stop_recording(camera_id: str, ctx: AppContext = Depends(get_context)) -> MediaCreatedResponse:
    record = ctx.recorder.stop(camera_id)
    if record is None:
        raise HTTPException(status_code=409, detail="not recording")
    return MediaCreatedResponse(media_id=record.id)


@router.post("/cameras/{camera_id:path}/detect", response_model=DetectionResult)
async def detect_objects(
    camera_id: str, ctx: AppContext = Depends(get_context)
) -> DetectionResult:
    """Run the active detection model on the camera's latest frame and return
    bounding boxes (where + what). 200 with ``detector_active=false`` when no
    detection model is active — the UI just shows no overlay."""
    import anyio

    active_model = ctx.store.active_model()
    detector_active = ctx.inference.detection_active
    if not detector_active:
        return DetectionResult(camera_id=camera_id, detector_active=False)
    buffer = ctx.manager.get_buffer(camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="camera not found")
    frame = await anyio.to_thread.run_sync(buffer.await_latest, -1, 2.0)
    if frame is None:
        raise HTTPException(status_code=503, detail="no frame available")
    detections = await anyio.to_thread.run_sync(ctx.inference.detect, frame.image)
    boxes = [
        DetectionBox(
            label=d.label, confidence=d.confidence, cx=d.cx, cy=d.cy, w=d.w, h=d.h
        )
        for d in (detections or [])
    ]
    return DetectionResult(
        camera_id=camera_id,
        detector_active=True,
        model_name=active_model.name if active_model else None,
        boxes=boxes,
    )


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
    )


@router.get("/timelapse-presets", response_model=list[dict[str, object]])
def list_timelapse_presets() -> list[dict[str, object]]:
    return printer_presets()


@router.get("/timelapse", response_model=list[TimelapseInfo])
def list_timelapses(
    camera_id: str | None = None,
    limit: int = 100,
    ctx: AppContext = Depends(get_context),
) -> list[TimelapseInfo]:
    summaries = ctx.store.timelapse_analysis_summaries()
    return [
        _timelapse_info(ctx, r, summaries.get(r.id or 0, (0, "")))
        for r in ctx.timelapse.list(camera_id, limit)
    ]


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
    if analysis_enabled and not ctx.analyzer.enabled:
        raise HTTPException(
            status_code=409,
            detail="Enable and configure Ollama on the Models page before printer analysis",
        )
    record = ctx.timelapse.start(
        camera_id,
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
    if record is None:
        raise HTTPException(status_code=503, detail="camera unavailable")
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
    run = ctx.training.train(body.dataset_id, body.base_model, body.epochs, body.image_size)
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
    )


@router.post("/system/reload", response_model=list[CameraInfo])
async def system_reload(ctx: AppContext = Depends(get_context)) -> list[CameraInfo]:
    """Re-scan devices and restart all local capture workers (no process restart)."""
    for cam in ctx.manager.list():
        ctx.manager.restart(cam.descriptor.id)
    ctx.manager.discover()
    ctx.manager.start_all()
    await ctx.cluster.refresh(force=True)
    return await _aggregate_cameras(ctx, "all")


async def _ai_info(ctx: AppContext) -> AIInfo:
    import anyio

    reachable, model = await anyio.to_thread.run_sync(ctx.analyzer.health)
    ai = ctx.config.ai
    return AIInfo(
        enabled=ai.enabled,
        reachable=reachable,
        model=ai.model,
        model_present=model is not None,
        base_url=ai.base_url,
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
    ctx.config.save()
    return await _ai_info(ctx)


async def _ollama_models_info(ctx: AppContext) -> OllamaModelsInfo:
    import anyio

    reachable, installed = await anyio.to_thread.run_sync(ctx.analyzer.installed_models)
    return OllamaModelsInfo(
        reachable=reachable,
        base_url=ctx.config.ai.base_url,
        active_model=ctx.config.ai.model,
        installed=installed,
    )


@router.get("/ai/models", response_model=OllamaModelsInfo)
async def ai_models(ctx: AppContext = Depends(get_context)) -> OllamaModelsInfo:
    """List models installed in the configured Ollama backend."""
    return await _ollama_models_info(ctx)


@router.post("/ai/pull", response_model=OllamaModelsInfo)
async def ai_pull(
    body: AIModelRequest, ctx: AppContext = Depends(get_context)
) -> OllamaModelsInfo:
    """Download a model into Ollama (can take minutes for large models)."""
    import anyio

    ok, status = await anyio.to_thread.run_sync(ctx.analyzer.pull, body.model)
    if not ok:
        raise HTTPException(status_code=502, detail=f"ollama pull failed: {status}")
    return await _ollama_models_info(ctx)


@router.post("/ai/load", response_model=AIInfo)
async def ai_load(body: AIModelRequest, ctx: AppContext = Depends(get_context)) -> AIInfo:
    """Warm a model into Ollama's memory ('start' it) for fast first inference."""
    import anyio

    ok = await anyio.to_thread.run_sync(ctx.analyzer.load, body.model)
    if not ok:
        raise HTTPException(
            status_code=502, detail="ollama load failed (model present and reachable?)"
        )
    return await _ai_info(ctx)


@router.get("/update", response_model=UpdateInfo)
async def update_info(ctx: AppContext = Depends(get_context)) -> UpdateInfo:
    """Cached check for a newer TailCam release (for the dashboard banner)."""
    import anyio

    from tailcam import update as upd

    current, latest, available = await anyio.to_thread.run_sync(upd.update_available)
    return UpdateInfo(current=current, latest=latest, available=available)
