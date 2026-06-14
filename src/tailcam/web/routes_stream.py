"""MJPEG streaming, single-frame snapshots, and media file serving."""

from __future__ import annotations

from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from tailcam.camera.transforms import StreamTransform
from tailcam.streaming.encoder import encode_jpeg
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context

router = APIRouter()


@router.get("/stream/{camera_id:path}.mjpg")
def mjpeg_stream(
    camera_id: str,
    fps: int = Query(default=15, ge=1, le=60),
    zoom: float = Query(default=1.0, ge=1.0, le=8.0),
    pan_x: float = Query(default=0.5, ge=0.0, le=1.0),
    pan_y: float = Query(default=0.5, ge=0.0, le=1.0),
    w: int = Query(default=0, ge=0, le=3840),
    q: int = Query(default=80, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
) -> StreamingResponse:
    buffer = ctx.manager.get_buffer(camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="camera not found")
    transform = StreamTransform(zoom=zoom, pan_x=pan_x, pan_y=pan_y, max_width=w)
    generator = ctx.mjpeg.stream(buffer, transform, fps, q)
    return StreamingResponse(generator, media_type=ctx.mjpeg.media_type)


@router.get("/stream/{camera_id:path}/snapshot.jpg")
async def snapshot_jpg(
    camera_id: str, ctx: AppContext = Depends(get_context)
) -> Response:
    buffer = ctx.manager.get_buffer(camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="camera not found")
    frame = await anyio.to_thread.run_sync(buffer.await_latest, -1, 3.0)
    if frame is None:
        raise HTTPException(status_code=503, detail="no frame available")
    jpeg = await anyio.to_thread.run_sync(encode_jpeg, frame.image, 85)
    return Response(content=jpeg, media_type="image/jpeg")


@router.get("/media/{media_id}/file")
def media_file(media_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    record = ctx.gallery.get(media_id)
    if record is None or not Path(record.path).exists():
        raise HTTPException(status_code=404, detail="media not found")
    return FileResponse(record.path)


@router.get("/media/{media_id}/thumbnail")
def media_thumbnail(media_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    record = ctx.gallery.get(media_id)
    if record is None or not record.thumbnail or not Path(record.thumbnail).exists():
        raise HTTPException(status_code=404, detail="thumbnail not found")
    return FileResponse(record.thumbnail)


@router.get("/events/{event_id}/thumbnail")
def event_thumbnail(event_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    rec = ctx.store.get_motion_event(event_id)
    if rec is None or not rec.thumb_path or not Path(rec.thumb_path).exists():
        raise HTTPException(status_code=404, detail="event thumbnail not found")
    return FileResponse(rec.thumb_path)


@router.get("/timelapse/{tl_id}/file")
def timelapse_file(tl_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    rec = ctx.store.get_timelapse(tl_id)
    if rec is None or not rec.video_path or not Path(rec.video_path).exists():
        raise HTTPException(status_code=404, detail="timelapse video not found")
    return FileResponse(rec.video_path, media_type="video/mp4")


@router.get("/timelapse/{tl_id}/thumbnail")
def timelapse_thumbnail(tl_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    rec = ctx.store.get_timelapse(tl_id)
    if rec is None or not rec.thumb_path or not Path(rec.thumb_path).exists():
        raise HTTPException(status_code=404, detail="timelapse thumbnail not found")
    return FileResponse(rec.thumb_path)
