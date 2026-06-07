"""REST API endpoints under /api."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from anycam import __version__
from anycam.camera.manager import ManagedCamera
from anycam.web.context import AppContext
from anycam.web.deps import get_context
from anycam.web.schemas import (
    CameraInfo,
    CameraSettingsUpdate,
    HostInfo,
    MediaCreatedResponse,
    MediaInfo,
    MotionEventInfo,
    OkResponse,
    SystemInfo,
    TransformModel,
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
    if scope != "local":
        await ctx.cluster.refresh(force=True)
    return await _aggregate_cameras(ctx, scope)


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
        media_bytes=ctx.gallery.total_bytes(),
    )
