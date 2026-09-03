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
    fps: int | None = Query(default=None, ge=1, le=60),
    zoom: float = Query(default=1.0, ge=1.0, le=8.0),
    pan_x: float = Query(default=0.5, ge=0.0, le=1.0),
    pan_y: float = Query(default=0.5, ge=0.0, le=1.0),
    w: int | None = Query(default=None, ge=0, le=3840),
    q: int | None = Query(default=None, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
) -> StreamingResponse:
    """MJPEG stream. fps / q (quality) / w (max width) default to the camera's
    device-wide stream settings; a client may only go *lower* (dashboard tiles
    ask for a low-bandwidth stream), never above what the camera is set to."""
    buffer = ctx.manager.get_buffer(camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="camera not found")
    settings = ctx.manager.effective_stream_for(camera_id) or {}
    cam_fps = int(settings.get("fps", 15))
    cam_q = int(settings.get("quality", 80))
    cam_w = int(settings.get("max_width", 0))
    eff_fps = min(fps, cam_fps) if fps else cam_fps
    eff_q = min(q, cam_q) if q else cam_q
    if w:
        eff_w = min(w, cam_w) if cam_w else w
    else:
        eff_w = cam_w
    transform = StreamTransform(zoom=zoom, pan_x=pan_x, pan_y=pan_y, max_width=eff_w)
    generator = ctx.mjpeg.stream(buffer, transform, eff_fps, eff_q)
    return StreamingResponse(generator, media_type=ctx.mjpeg.media_type)


@router.get("/stream/{camera_id:path}/snapshot.jpg")
async def snapshot_jpg(
    camera_id: str,
    zoom: float = Query(default=1.0, ge=1.0, le=8.0),
    pan_x: float = Query(default=0.5, ge=0.0, le=1.0),
    pan_y: float = Query(default=0.5, ge=0.0, le=1.0),
    w: int | None = Query(default=None, ge=0, le=3840),
    q: int | None = Query(default=None, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
) -> Response:
    """One JPEG frame. Accepts the same view params as the MJPEG stream (zoom /
    pan / max width / quality) so the snapshot-polling viewer used on iOS and
    Safari honors zoom and the low-bandwidth tile size instead of pulling a
    full-resolution frame per poll."""
    buffer = ctx.manager.get_buffer(camera_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="camera not found")
    settings = ctx.manager.effective_stream_for(camera_id) or {}
    cam_q = int(settings.get("quality", 85))
    cam_w = int(settings.get("max_width", 0))
    eff_q = min(q, cam_q) if q else cam_q
    if w:
        eff_w = min(w, cam_w) if cam_w else w
    else:
        eff_w = cam_w
    frame = await anyio.to_thread.run_sync(buffer.await_latest, -1, 3.0)
    if frame is None:
        raise HTTPException(status_code=503, detail="no frame available")
    transform = StreamTransform(zoom=zoom, pan_x=pan_x, pan_y=pan_y, max_width=eff_w)

    def _render() -> bytes:
        image = frame.image
        if transform != StreamTransform():
            image = transform.apply(image)
        return encode_jpeg(image, eff_q)

    jpeg = await anyio.to_thread.run_sync(_render)
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


@router.get("/timelapse/{tl_id}/smooth")
def timelapse_smooth(tl_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    rec = ctx.store.get_timelapse(tl_id)
    if rec is None or not rec.smooth_path or not Path(rec.smooth_path).exists():
        raise HTTPException(status_code=404, detail="smoothed timelapse not found")
    return FileResponse(rec.smooth_path, media_type="video/mp4")


@router.get("/timelapse/{tl_id}/frame/{frame_number}")
def timelapse_frame(
    tl_id: int, frame_number: int, ctx: AppContext = Depends(get_context)
) -> FileResponse:
    """Serve a single captured frame (e.g. the evidence frame a print-failure
    analysis flagged). Frames are the retained ``NNNNNN.jpg`` capture stills."""
    rec = ctx.store.get_timelapse(tl_id)
    if rec is None or not rec.frames_dir or frame_number < 0:
        raise HTTPException(status_code=404, detail="frame not found")
    frame = Path(rec.frames_dir) / f"{frame_number:06d}.jpg"
    if not frame.exists():
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(frame, media_type="image/jpeg")


@router.get("/datasets/sample/{sample_id}/thumbnail")
def dataset_sample_thumb(sample_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    rec = ctx.store.get_sample(sample_id)
    candidate = (rec.thumb or rec.path) if rec else None
    if rec is None or not candidate or not Path(candidate).exists():
        raise HTTPException(status_code=404, detail="sample not found")
    return FileResponse(candidate)


@router.get("/datasets/sample/{sample_id}/image")
def dataset_sample_image(sample_id: int, ctx: AppContext = Depends(get_context)) -> FileResponse:
    rec = ctx.store.get_sample(sample_id)
    if rec is None or not Path(rec.path).exists():
        raise HTTPException(status_code=404, detail="sample not found")
    return FileResponse(rec.path)
