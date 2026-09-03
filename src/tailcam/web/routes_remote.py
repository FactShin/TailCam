"""Storage-node endpoints: capture a *peer's* camera on this node.

A source node whose ``[storage] node`` points here calls these to record or
timelapse one of its cameras. This node pulls that camera's MJPEG stream from
the source (which must be a discovered tailnet peer — never an arbitrary URL)
and runs the normal recorder / timelapse worker against the pulled frames, so
the files, thumbnails, and database rows live here. The resulting media and
timelapses carry ``source_host`` so the UI can attribute them to the camera's
own node while serving them from this one.
"""

from __future__ import annotations

from functools import partial

from fastapi import APIRouter, Depends, HTTPException

from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.routes_api import _timelapse_info
from tailcam.web.schemas import (
    MediaCreatedResponse,
    OkResponse,
    RemoteRecordingStart,
    RemoteRecordingStatus,
    RemoteTimelapseStart,
    TimelapseInfo,
)

router = APIRouter(prefix="/api/remote")


async def _source_base(ctx: AppContext, source_key: str) -> str:
    base = ctx.cluster.peer_base(source_key)
    if base is None:
        await ctx.cluster.refresh(force=True)
        base = ctx.cluster.peer_base(source_key)
    if base is None:
        raise HTTPException(
            status_code=404,
            detail=f"source node '{source_key}' is not visible from this storage node",
        )
    return base


def _session_key(source_key: str, camera_id: str) -> str:
    return ctx_key(source_key, camera_id)


def ctx_key(source_key: str, camera_id: str) -> str:
    return f"{source_key}|{camera_id}"


@router.post(
    "/{source_key}/cameras/{camera_id:path}/recording/start", response_model=OkResponse
)
async def remote_recording_start(
    source_key: str,
    camera_id: str,
    body: RemoteRecordingStart,
    ctx: AppContext = Depends(get_context),
) -> OkResponse:
    await _source_base(ctx, source_key)
    key = _session_key(source_key, camera_id)
    buffer = ctx.remote_feeds.get_buffer(source_key, camera_id, body.fps)
    if buffer is None:
        raise HTTPException(status_code=502, detail="could not open the source camera stream")
    started = ctx.recorder.start(
        key,
        fps=body.fps,
        trigger=body.trigger,
        buffer=buffer,
        reacquire=partial(ctx.remote_feeds.get_buffer, source_key, camera_id, body.fps),
        media_camera_id=camera_id,
        source_host=body.source_host,
    )
    if not started:
        raise HTTPException(status_code=409, detail="already recording")
    return OkResponse(detail=f"recording {camera_id} from {body.source_host or source_key}")


@router.post(
    "/{source_key}/cameras/{camera_id:path}/recording/stop", response_model=MediaCreatedResponse
)
def remote_recording_stop(
    source_key: str, camera_id: str, ctx: AppContext = Depends(get_context)
) -> MediaCreatedResponse:
    record = ctx.recorder.stop(_session_key(source_key, camera_id))
    if record is None:
        raise HTTPException(status_code=409, detail="not recording")
    return MediaCreatedResponse(media_id=record.id)


@router.get(
    "/{source_key}/cameras/{camera_id:path}/recording", response_model=RemoteRecordingStatus
)
def remote_recording_status(
    source_key: str, camera_id: str, ctx: AppContext = Depends(get_context)
) -> RemoteRecordingStatus:
    return RemoteRecordingStatus(
        recording=ctx.recorder.is_recording(_session_key(source_key, camera_id))
    )


@router.post(
    "/{source_key}/cameras/{camera_id:path}/timelapse/start", response_model=TimelapseInfo
)
async def remote_timelapse_start(
    source_key: str,
    camera_id: str,
    body: RemoteTimelapseStart,
    ctx: AppContext = Depends(get_context),
) -> TimelapseInfo:
    await _source_base(ctx, source_key)
    if body.analysis_enabled and not ctx.analyzer.enabled:
        raise HTTPException(
            status_code=409,
            detail="printer analysis needs Ollama enabled on the storage node",
        )
    # Timelapse frames are seconds apart: pull the source at a gentle rate
    # (never above its own stream setting; the source caps it anyway).
    pull_fps = max(1, min(int(body.source_fps or 5), 10))
    buffer = ctx.remote_feeds.get_buffer(source_key, camera_id, pull_fps)
    if buffer is None:
        raise HTTPException(status_code=502, detail="could not open the source camera stream")
    record = ctx.timelapse.start(
        camera_id,
        name=body.name,
        interval_seconds=body.interval_seconds,
        output_fps=body.output_fps,
        duration_seconds=body.duration_seconds,
        jpeg_quality=body.jpeg_quality,
        max_frames=body.max_frames,
        auto_smooth=body.auto_smooth,
        smooth_target_fps=body.smooth_target_fps,
        smooth_interpolate=body.smooth_interpolate,
        smooth_deflicker=body.smooth_deflicker,
        smooth_engine=body.smooth_engine,
        smooth_quality=body.smooth_quality,
        analysis_enabled=body.analysis_enabled,
        analysis_cadence_seconds=body.analysis_cadence_seconds,
        source_host=body.source_host or source_key,
        buffer=buffer,
        reacquire=partial(ctx.remote_feeds.get_buffer, source_key, camera_id, pull_fps),
        camera_name=body.camera_name,
    )
    if record is None:
        raise HTTPException(status_code=503, detail="could not start capture")
    return _timelapse_info(ctx, record)
