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
    # Why the camera is offline/degraded (e.g. "failed to open device"), if known.
    last_error: str | None = None
    # Multi-host: which node owns this camera, and the prefix to reach its
    # stream/controls through this node ("" = local, "/proxy/<key>" = remote).
    host: str = ""
    proxy_prefix: str = ""


class HostInfo(BaseModel):
    host: str
    kind: str  # "local" | "peer"
    online: bool
    version: str | None = None
    camera_count: int = 0
    proxy_prefix: str = ""


class MediaInfo(BaseModel):
    id: int
    camera_id: str
    media_type: str
    created_ts: float
    trigger: str
    size_bytes: int
    has_thumbnail: bool
    # Multi-host: owning node + prefix to reach the file/thumbnail/delete through
    # the node you're viewing ("" = local).
    host: str = ""
    proxy_prefix: str = ""


class MotionEventInfo(BaseModel):
    id: int
    camera_id: str
    start_ts: float
    end_ts: float | None
    peak_score: float
    recording_id: int | None
    label: str | None = None  # AI: person/animal/vehicle/… (None = not analyzed)
    description: str | None = None
    confidence: float | None = None
    has_thumb: bool = False
    host: str = ""
    proxy_prefix: str = ""


class AIInfo(BaseModel):
    enabled: bool
    reachable: bool
    model: str
    model_present: bool


class SystemInfo(BaseModel):
    version: str
    host: str = ""  # this node's identity (used for peer discovery)
    tailscale_installed: bool
    tailscale_running: bool
    access_url: str
    local_url: str
    media_bytes: int
    hidden_count: int = 0  # cameras the user has deleted/forgotten


class UpdateInfo(BaseModel):
    current: str
    latest: str | None = None
    available: bool = False


class OkResponse(BaseModel):
    ok: bool = True
    detail: str | None = None


class MediaCreatedResponse(BaseModel):
    ok: bool = True
    media_id: int | None = None
