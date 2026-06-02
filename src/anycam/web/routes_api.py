"""REST API endpoints under /api."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from anycam import __version__
from anycam.camera.manager import ManagedCamera
from anycam.web.context import AppContext
from anycam.web.deps import get_context
from anycam.web.schemas import (
    CameraInfo,
    CameraSettingsUpdate,
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
    )


@router.get("/cameras", response_model=list[CameraInfo])
def list_cameras(ctx: AppContext = Depends(get_context)) -> list[CameraInfo]:
    return [_camera_info(ctx, cam) for cam in ctx.manager.list()]


@router.post("/cameras/refresh", response_model=list[CameraInfo])
def refresh_cameras(ctx: AppContext = Depends(get_context)) -> list[CameraInfo]:
    ctx.manager.discover()
    return [_camera_info(ctx, cam) for cam in ctx.manager.list()]


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


def _media_info(record) -> MediaInfo:
    return MediaInfo(
        id=record.id,
        camera_id=record.camera_id,
        media_type=record.media_type,
        created_ts=record.created_ts,
        trigger=record.trigger,
        size_bytes=record.size_bytes,
        has_thumbnail=bool(record.thumbnail),
    )


@router.get("/media", response_model=list[MediaInfo])
def list_media(
    camera_id: str | None = None,
    media_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: AppContext = Depends(get_context),
) -> list[MediaInfo]:
    records = ctx.gallery.list(camera_id, media_type, limit, offset)
    return [_media_info(r) for r in records]


@router.delete("/media/{media_id}", response_model=OkResponse)
def delete_media(media_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    if not ctx.gallery.delete(media_id):
        raise HTTPException(status_code=404, detail="media not found")
    return OkResponse(detail="deleted")


@router.get("/events", response_model=list[MotionEventInfo])
def list_events(
    camera_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: AppContext = Depends(get_context),
) -> list[MotionEventInfo]:
    events = ctx.event_log.list(camera_id, limit, offset)
    return [
        MotionEventInfo(
            id=e.id,
            camera_id=e.camera_id,
            start_ts=e.start_ts,
            end_ts=e.end_ts,
            peak_score=e.peak_score,
            recording_id=e.recording_id,
        )
        for e in events
    ]


@router.get("/system", response_model=SystemInfo)
def system_info(ctx: AppContext = Depends(get_context)) -> SystemInfo:
    status = ctx.tailscale.status()
    port = ctx.config.server.port
    return SystemInfo(
        version=__version__,
        tailscale_installed=status.installed,
        tailscale_running=status.running,
        access_url=ctx.tailscale.access_url(port, ctx.served, ctx.config.tailscale.serve_port),
        local_url=f"http://localhost:{port}/",
        media_bytes=ctx.gallery.total_bytes(),
    )
