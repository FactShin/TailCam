"""REST API endpoints under /api."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from tailcam import __version__
from tailcam.camera.manager import ManagedCamera
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.schemas import (
    AIInfo,
    CameraInfo,
    CameraSettingsUpdate,
    HostInfo,
    MediaCreatedResponse,
    MediaInfo,
    MotionEventInfo,
    OkResponse,
    PostprocessInfo,
    SystemInfo,
    TimelapseInfo,
    TimelapseSmoothRequest,
    TimelapseStartRequest,
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


def _timelapse_info(ctx: AppContext, r) -> TimelapseInfo:
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
        host=ctx.local_host,
        proxy_prefix="",
    )


@router.get("/timelapse", response_model=list[TimelapseInfo])
def list_timelapses(
    camera_id: str | None = None,
    limit: int = 100,
    ctx: AppContext = Depends(get_context),
) -> list[TimelapseInfo]:
    return [_timelapse_info(ctx, r) for r in ctx.timelapse.list(camera_id, limit)]


@router.post("/cameras/{camera_id:path}/timelapse/start", response_model=TimelapseInfo)
def start_timelapse(
    camera_id: str,
    req: TimelapseStartRequest | None = None,
    ctx: AppContext = Depends(get_context),
) -> TimelapseInfo:
    req = req or TimelapseStartRequest()
    record = ctx.timelapse.start(
        camera_id,
        name=req.name,
        interval_seconds=req.interval_seconds,
        output_fps=req.output_fps,
        duration_seconds=req.duration_seconds,
    )
    if record is None:
        raise HTTPException(status_code=503, detail="camera unavailable")
    return _timelapse_info(ctx, record)


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
        tl_id, target_fps=req.target_fps, interpolate=req.interpolate, deflicker=req.deflicker
    )
    if record is None:
        raise HTTPException(status_code=404, detail="timelapse not found or has no frames")
    return _timelapse_info(ctx, record)


@router.delete("/timelapse/{tl_id}", response_model=OkResponse)
def delete_timelapse(tl_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    if not ctx.timelapse.delete(tl_id):
        raise HTTPException(status_code=404, detail="timelapse not found")
    return OkResponse(detail="deleted")


@router.get("/postprocess", response_model=PostprocessInfo)
def postprocess_info(ctx: AppContext = Depends(get_context)) -> PostprocessInfo:
    """ffmpeg availability for the smoothing feature (dashboard status panel)."""
    from tailcam.timelapse.ffmpeg import ffmpeg_source, ffmpeg_version

    source = ffmpeg_source()
    return PostprocessInfo(
        available=source != "missing",
        source=source,
        version=ffmpeg_version(),
        default_target_fps=ctx.config.timelapse.smooth_target_fps,
    )


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


@router.get("/ai", response_model=AIInfo)
async def ai_info(ctx: AppContext = Depends(get_context)) -> AIInfo:
    """AI analyzer status for the dashboard (Ollama reachable? model present?)."""
    import anyio

    reachable, model = await anyio.to_thread.run_sync(ctx.analyzer.health)
    return AIInfo(
        enabled=ctx.config.ai.enabled,
        reachable=reachable,
        model=ctx.config.ai.model,
        model_present=model is not None,
    )


@router.get("/update", response_model=UpdateInfo)
async def update_info(ctx: AppContext = Depends(get_context)) -> UpdateInfo:
    """Cached check for a newer TailCam release (for the dashboard banner)."""
    import anyio

    from tailcam import update as upd

    current, latest, available = await anyio.to_thread.run_sync(upd.update_available)
    return UpdateInfo(current=current, latest=latest, available=available)
