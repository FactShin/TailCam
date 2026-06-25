"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    node_key: str
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
    base_url: str = ""
    provider: str = "ollama"


class AIUpdate(BaseModel):
    enabled: bool | None = None
    model: str | None = None
    base_url: str | None = None
    provider: str | None = None


class PluginEntry(BaseModel):
    id: str
    name: str
    kind: str  # ai | notification | other
    description: str = ""
    version: str = ""
    builtin: bool = False


class ProviderEntry(BaseModel):
    id: str
    name: str
    description: str = ""


class ChannelEntry(BaseModel):
    id: str
    name: str


class PluginsInfo(BaseModel):
    plugins: list[PluginEntry] = Field(default_factory=list)
    analyzer_providers: list[ProviderEntry] = Field(default_factory=list)
    notification_channels: list[ChannelEntry] = Field(default_factory=list)
    active_provider: str = "ollama"
    errors: list[str] = Field(default_factory=list)


# -- home-automation integrations --


class IntegrationCamera(BaseModel):
    id: str
    name: str


class HomeKitStatus(BaseModel):
    enabled: bool
    available: bool  # HAP-python installed
    ffmpeg_present: bool  # needed on the host for live video
    running: bool
    paired: bool
    pin: str = ""
    setup_uri: str | None = None
    setup_qr: str | None = None  # inline SVG of the pairing QR
    bridge_name: str = "TailCam"
    port: int = 51826
    selected: list[str] = Field(default_factory=list)  # configured subset; [] = all
    cameras: list[IntegrationCamera] = Field(default_factory=list)  # all available cameras


class HomeKitUpdate(BaseModel):
    enabled: bool | None = None
    bridge_name: str | None = None
    port: int | None = None
    cameras: list[str] | None = None
    regenerate_pin: bool | None = None


class HACameraEntry(BaseModel):
    camera_id: str
    name: str
    mjpeg_url: str
    still_image_url: str


class HomeAssistantStatus(BaseModel):
    enabled: bool
    mqtt_available: bool  # paho-mqtt installed
    mqtt_configured: bool  # broker host set
    mqtt_connected: bool
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_tls: bool = False
    discovery_prefix: str = "homeassistant"
    node_id: str = "tailcam"
    publish_motion: bool = True
    publish_status: bool = True
    base_url: str = ""
    cameras: list[HACameraEntry] = Field(default_factory=list)
    yaml: str = ""


class HomeAssistantUpdate(BaseModel):
    enabled: bool | None = None
    mqtt_host: str | None = None
    mqtt_port: int | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool | None = None
    discovery_prefix: str | None = None
    node_id: str | None = None
    publish_motion: bool | None = None
    publish_status: bool | None = None


class IntegrationsInfo(BaseModel):
    homekit: HomeKitStatus
    homeassistant: HomeAssistantStatus


class OllamaModelsInfo(BaseModel):
    reachable: bool
    base_url: str = ""
    active_model: str = ""
    installed: list[str] = Field(default_factory=list)


class AIModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class AIPullStatus(BaseModel):
    model: str = ""
    active: bool = False  # a download is in progress
    status: str = "idle"  # idle | pulling | success | error
    completed: int = 0
    total: int = 0
    percent: float = 0.0
    detail: str = ""
    error: str | None = None


class TimelapseStartRequest(BaseModel):
    name: str | None = None
    interval_seconds: float | None = Field(default=None, ge=0.1, le=3600)
    output_fps: int | None = Field(default=None, ge=1, le=120)
    duration_seconds: float = Field(default=0.0, ge=0, le=604800)
    jpeg_quality: int | None = Field(default=None, ge=1, le=100)
    max_frames: int | None = Field(default=None, ge=0, le=10_000_000)
    auto_smooth: bool | None = None
    smooth_target_fps: int | None = Field(default=None, ge=1, le=120)
    smooth_interpolate: bool | None = None
    smooth_deflicker: bool | None = None
    smooth_engine: Literal["ffmpeg", "rife"] | None = None
    smooth_quality: Literal["standard", "high", "maximum"] | None = None
    analysis_enabled: bool | None = None
    analysis_cadence_seconds: float | None = Field(default=None, ge=1, le=3600)


class TimelapseInfo(BaseModel):
    id: int
    camera_id: str
    name: str
    state: str  # capturing | encoding | complete | interrupted | error
    mode: str
    interval_seconds: float
    output_fps: int
    frames_captured: int
    created_ts: float
    start_ts: float
    end_ts: float | None = None
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_thumb: bool = False
    # Smoothed (interpolated) variant.
    smooth_state: str = "none"  # none | processing | complete | error
    has_smooth: bool = False
    smooth_size_bytes: int = 0
    smooth_engine: str = ""  # ffmpeg | rife
    jpeg_quality: int = 90
    max_frames: int = 0
    auto_smooth: bool = False
    smooth_target_fps: int = 60
    smooth_interpolate: bool = True
    smooth_deflicker: bool = True
    smooth_quality: str = "high"
    analysis_enabled: bool = False
    analysis_cadence_seconds: float = 60.0
    analysis_event_count: int = 0
    analysis_latest_state: str = ""
    host: str = ""
    proxy_prefix: str = ""


class TimelapseAnalysisEventInfo(BaseModel):
    id: int
    timelapse_id: int
    frame_number: int
    state: Literal["healthy", "possible_failure", "failure", "uncertain"]
    confidence: float
    description: str
    created_ts: float


class TimelapseSmoothRequest(BaseModel):
    target_fps: int | None = Field(default=None, ge=1, le=120)
    interpolate: bool | None = None
    deflicker: bool | None = None
    engine: Literal["ffmpeg", "rife"] | None = None
    quality: Literal["standard", "high", "maximum"] | None = None


class EngineInfo(BaseModel):
    id: str  # ffmpeg | rife
    label: str
    available: bool
    source: str  # system | bundled | missing
    version: str | None = None


class PostprocessInfo(BaseModel):
    available: bool  # at least one engine usable (ffmpeg is always there)
    default_engine: str  # ffmpeg | rife
    default_target_fps: int = 60
    engines: list[EngineInfo] = []


class PostprocessSettings(BaseModel):
    default_engine: str | None = None  # ffmpeg | rife


# -- model training --------------------------------------------------------


class DatasetInfo(BaseModel):
    id: int
    name: str
    task: str  # classification | detection
    created_ts: float
    note: str = ""
    sample_count: int = 0
    label_counts: dict[str, int] = {}
    # Detection datasets: how many samples carry boxes + boxes-per-class.
    annotated_count: int = 0
    box_label_counts: dict[str, int] = {}


class DatasetCreate(BaseModel):
    name: str
    note: str = ""
    task: Literal["classification", "detection"] = "classification"


class SampleInfo(BaseModel):
    id: int
    dataset_id: int
    label: str | None = None
    source: str
    camera_id: str
    host: str = ""
    created_ts: float
    confidence: float | None = None
    has_thumb: bool = False
    annotation_count: int = 0  # detection: number of boxes drawn on this sample


class SampleRelabel(BaseModel):
    label: str | None = None  # None clears the label


class AnnotationBox(BaseModel):
    """A bounding box on a detection sample, normalized 0..1 (center + size)."""

    label: str = Field(min_length=1, max_length=64)
    cx: float = Field(ge=0.0, le=1.0)
    cy: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class SampleAnnotations(BaseModel):
    sample_id: int
    boxes: list[AnnotationBox] = []


class SampleAnnotationsUpdate(BaseModel):
    boxes: list[AnnotationBox] = Field(default_factory=list, max_length=200)


class DetectionBox(AnnotationBox):
    confidence: float = Field(ge=0.0, le=1.0)


class DetectionResult(BaseModel):
    camera_id: str
    detector_active: bool  # False = no detection model is active
    model_name: str | None = None
    boxes: list[DetectionBox] = []


class ModelInfo(BaseModel):
    id: int
    name: str
    kind: str  # base | trained | byo
    task: str = "classification"  # classification | detection
    active: bool = False
    base_model: str = ""
    classes: list[str] = []
    metrics: dict = {}
    created_ts: float
    has_artifact: bool = False


class ModelRegister(BaseModel):
    name: str
    path: str
    task: Literal["classification", "detection"] = "classification"


class CollectionUpdate(BaseModel):
    enabled: bool | None = None
    interval_seconds: float | None = None
    auto_label: bool | None = None
    active_dataset_id: int | None = None


class TrainRequest(BaseModel):
    dataset_id: int
    base_model: str | None = None
    epochs: int | None = None
    image_size: int | None = None


class TrainingRunInfo(BaseModel):
    id: int
    dataset_id: int
    model_id: int | None = None
    base_model: str
    status: str  # queued | preparing | training | complete | error | stopped
    epochs: int
    epoch: int
    metrics: dict = {}
    log: str = ""
    created_ts: float
    started_ts: float | None = None
    ended_ts: float | None = None


class TrainingInfo(BaseModel):
    engine_available: bool
    framework: str = "ultralytics"
    version: str | None = None
    device: str = "none"
    collecting: bool = False
    collect_enabled: bool = False
    collect_interval_seconds: float = 30.0
    auto_label: bool = True
    active_dataset_id: int = 0
    active_model_id: int = 0
    classes: list[str] = []
    total_samples: int = 0
    dataset_count: int = 0
    model_count: int = 0
    collected_session: int = 0


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


class PrincipalInfo(BaseModel):
    actor: str
    display_name: str | None = None
    source: str
    verified: bool
    roles: list[str] = Field(default_factory=list)


class NodeIssueInfo(BaseModel):
    code: str
    severity: str
    summary: str
    detail: str | None = None


class NodeCapabilitiesInfo(BaseModel):
    api_version: str
    capabilities: list[str]
    actions: list[str]
    principal: PrincipalInfo


class NodeHealthInfo(BaseModel):
    host: str
    version: str
    platform: str
    python_version: str
    uptime_seconds: float
    tailscale_installed: bool
    tailscale_running: bool
    tailscale_served: bool
    access_url: str
    local_url: str
    camera_total: int
    camera_online: int
    camera_offline: int
    camera_degraded: int
    camera_recording: int
    media_bytes: int
    timelapse_bytes: int
    update_current: str
    update_latest: str | None = None
    update_available: bool
    ai_enabled: bool
    ai_reachable: bool
    ai_model: str
    ai_model_present: bool
    issues: list[NodeIssueInfo] = Field(default_factory=list)


class AuditEventInfo(BaseModel):
    id: int
    created_ts: float
    actor: str
    source: str
    action: str
    target: str
    result: str
    detail: str | None = None
    metadata: dict = Field(default_factory=dict)


class NodeActionResponse(BaseModel):
    action: str
    target: str
    result: str
    detail: str
    health: NodeHealthInfo


class NotificationsInfo(BaseModel):
    enabled: bool = False
    discord_webhook: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""
    notify_motion: bool = True
    notify_camera_offline: bool = True
    notify_training: bool = True
    min_confidence: float = 0.0
    labels: list[str] = Field(default_factory=list)
    cooldown_seconds: float = 60.0
    channels: list[str] = Field(default_factory=list)  # which channels are configured


class NotificationsUpdate(BaseModel):
    enabled: bool | None = None
    discord_webhook: str | None = None
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None
    notify_motion: bool | None = None
    notify_camera_offline: bool | None = None
    notify_training: bool | None = None
    min_confidence: float | None = None
    labels: list[str] | None = None
    cooldown_seconds: float | None = None


class OkResponse(BaseModel):
    ok: bool = True
    detail: str | None = None


class MediaCreatedResponse(BaseModel):
    ok: bool = True
    media_id: int | None = None
