"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from pydantic import BaseModel


class TransformModel(BaseModel):
    rotation: int = 0
    flip_h: bool = False
    flip_v: bool = False


class PropertiesModel(BaseModel):
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None


class CameraSettingsUpdate(BaseModel):
    name: str | None = None
    properties: PropertiesModel | None = None
    transform: TransformModel | None = None
    motion_enabled: bool | None = None


class CameraInfo(BaseModel):
    id: str
    name: str
    backend: str
    status: str
    fps: float
    width: int
    height: int
    recording: bool
    motion_enabled: bool
    properties: dict
    transform: TransformModel


class MediaInfo(BaseModel):
    id: int
    camera_id: str
    media_type: str
    created_ts: float
    trigger: str
    size_bytes: int
    has_thumbnail: bool


class MotionEventInfo(BaseModel):
    id: int
    camera_id: str
    start_ts: float
    end_ts: float | None
    peak_score: float
    recording_id: int | None


class SystemInfo(BaseModel):
    version: str
    tailscale_installed: bool
    tailscale_running: bool
    access_url: str
    local_url: str
    media_bytes: int


class OkResponse(BaseModel):
    ok: bool = True
    detail: str | None = None


class MediaCreatedResponse(BaseModel):
    ok: bool = True
    media_id: int | None = None
