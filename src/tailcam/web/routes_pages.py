"""Server-rendered HTML pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from tailcam.web.context import AppContext
from tailcam.web.deps import get_context

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, ctx: AppContext = Depends(get_context)) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request, "index.html", {"cameras": ctx.manager.list()}
    )


@router.get("/camera/{camera_id:path}", response_class=HTMLResponse)
def camera_page(
    request: Request, camera_id: str, ctx: AppContext = Depends(get_context)
) -> HTMLResponse:
    cam = ctx.manager.get(camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return _templates(request).TemplateResponse(request, "camera.html", {"camera": cam})


@router.get("/gallery", response_class=HTMLResponse)
def gallery_page(request: Request, ctx: AppContext = Depends(get_context)) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request, "gallery.html", {"cameras": ctx.manager.list()}
    )


@router.get("/events", response_class=HTMLResponse)
def events_page(request: Request, ctx: AppContext = Depends(get_context)) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request, "events.html", {"cameras": ctx.manager.list()}
    )
