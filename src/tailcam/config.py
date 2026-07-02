"""Application configuration backed by a human-editable TOML file.

App-level and per-camera *display* settings live here. Dynamic, queryable data
(camera registry, media index, motion events) lives in SQLite instead.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

import tomli_w

from tailcam import paths


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8088


@dataclass
class StreamConfig:
    default_fps: int = 15
    jpeg_quality: int = 80
    max_width: int = 1280


@dataclass
class MotionConfig:
    enabled: bool = False
    sensitivity: int = 50  # 1..100, higher = more sensitive
    min_area: int = 800
    sample_fps: int = 5
    cooldown_seconds: float = 5.0
    # Save a clip for every motion event. On by default — a motion log where
    # every row says "no clip" surprises people far more than a few MB of video
    # (retention/pruning is the knob for disk pressure, not silent no-ops).
    auto_record: bool = True
    record_tail_seconds: float = 5.0


@dataclass
class RetentionConfig:
    # Auto-cleanup is opt-in: TailCam never deletes media unless the user turns
    # it on (Settings → Recording & storage). The limits below only apply then.
    enabled: bool = False
    max_gb: float = 10.0
    max_age_days: int = 30


@dataclass
class StorageConfig:
    # Where recordings, snapshots, and thumbnails are written. Blank = the
    # default app data location (``<data-dir>/media``). Point this at an external
    # drive or NAS mount to keep video off the system disk. Existing media stays
    # where it was written; new media goes to the new location.
    media_dir: str = ""


@dataclass
class TailscaleConfig:
    auto_serve: bool = True
    # Tailnet-facing HTTPS port. 8443 keeps TailCam off the root URL (443) so it
    # won't clobber another app already served there. Tailscale allows 443,
    # 8443, and 10000 for serve/funnel.
    serve_port: int = 8443


@dataclass
class PeersConfig:
    # Multi-host: discover other TailCam nodes on the tailnet and show every
    # camera from any device. ``auto_discover`` probes online Tailscale peers;
    # ``static`` lists explicit peer base URLs (e.g. http://100.x.y.z:8088 or
    # https://host.tailnet.ts.net:8443) as a fallback/override.
    auto_discover: bool = True
    static: list[str] = field(default_factory=list)


@dataclass
class AIConfig:
    # Local vision-model analysis of motion events via Ollama. Cheap pixel
    # motion gates this, so the model is only consulted a frame or two per event.
    enabled: bool = False
    # Which analyzer provider plugin powers motion labeling. "ollama" is built in;
    # other providers can be added as plugins (see [plugins]).
    provider: str = "ollama"
    # Ollama endpoint. localhost when TailCam + Ollama run on the same box; point
    # at a tailnet host (e.g. http://mac-mini.your-tailnet.ts.net:11434) to let
    # one machine analyze the whole fleet's events.
    base_url: str = "http://localhost:11434"
    model: str = "moondream"  # small + fast; try qwen2.5vl or llava for better labels
    timeout: float = 20.0
    prompt: str = (
        "You are a security camera analyst. Look at this single frame and respond ONLY "
        "with JSON: {\"label\": one of [person, animal, vehicle, package, plant, nothing], "
        "\"confidence\": a number 0-1, \"description\": a short phrase}. No other text."
    )


@dataclass
class DetectionConfig:
    # Built-in live object detection: bounding boxes + labels (person, cup,
    # bottle, cat, dog, … — the 80 COCO classes) drawn over the camera view.
    # Plug and play: the first time it's needed the model downloads itself and
    # detection just starts working — no accounts, no cloud, no setup.
    #
    # Engine ladder (``engine = "auto"``): Ultralytics YOLO11n when the optional
    # training extra (torch) is installed — best accuracy, GPU-capable — else
    # OpenCV's built-in DNN running YOLOv4-tiny, which needs nothing beyond
    # TailCam's own dependencies. Enthusiasts can pin ``engine`` or swap
    # ``model`` for any Ultralytics detect model name/path.
    enabled: bool = True
    engine: str = "auto"  # auto | ultralytics | opencv
    model: str = ""  # advanced: Ultralytics model name/path override (e.g. yolo11s.pt)
    confidence: float = 0.45  # min box confidence, 0..1
    # Only report these labels (empty = all 80 COCO classes). e.g. ["person","dog"]
    classes: list[str] = field(default_factory=list)
    # Camera pages start with the detection overlay switched on.
    overlay_default: bool = True


@dataclass
class CamerasConfig:
    # Camera ids the user has deleted/forgotten; discovery skips these so
    # phantom devices (e.g. Raspberry Pi codec/ISP nodes) stay hidden.
    hidden: list[str] = field(default_factory=list)


@dataclass
class TimelapseConfig:
    # Timelapse capture + encoding. Tailored for long captures (e.g. 3D prints):
    # raw frames are kept on disk so post-processing (frame interpolation,
    # deflicker) can later stitch them into smooth, flowing motion.
    default_interval_seconds: float = 2.0  # seconds between captured frames
    default_output_fps: int = 30  # playback rate of the encoded video
    jpeg_quality: int = 90  # quality of the stored source frames
    # Safety cap so a forgotten capture can't fill the disk (0 = unlimited).
    max_frames: int = 0
    auto_smooth: bool = False
    # Post-processing ("Smooth"): motion-interpolate the captured frames up to
    # smooth_target_fps for flowing motion, and even out exposure flicker.
    smooth_target_fps: int = 60
    smooth_interpolate: bool = True
    smooth_deflicker: bool = True
    smooth_quality: str = "high"  # standard | high | maximum
    # Interpolation engine: "ffmpeg" (minterpolate, bundled, works everywhere) or
    # "rife" (rife-ncnn-vulkan, higher quality, GPU, must be installed). A failed
    # RIFE run automatically falls back to ffmpeg.
    smooth_engine: str = "ffmpeg"
    rife_path: str = ""  # explicit path to rife-ncnn-vulkan (else auto-detect)
    rife_model: str = "rife-v4.6"  # model folder name in the RIFE distribution
    analysis_enabled: bool = False
    analysis_cadence_seconds: float = 60.0


@dataclass
class TrainingConfig:
    # On-device model training from your own camera footage (optional, GPU). The
    # engine (Ultralytics/torch) is auto-detected; install it to enable.
    engine: str = "ultralytics"
    # Continuous dataset collection: sample a frame from every online camera on
    # this interval and add it to the active dataset for training.
    collect_enabled: bool = False
    collect_interval_seconds: float = 30.0
    auto_label: bool = True  # weak-label new samples with the Ollama model
    active_dataset_id: int = 0  # 0 = none
    classes: list[str] = field(
        default_factory=lambda: ["person", "animal", "vehicle", "package", "nothing"]
    )
    # Training run defaults.
    base_model: str = "yolo11n-cls.pt"  # downloaded on first train
    epochs: int = 30
    image_size: int = 224
    active_model_id: int = 0  # 0 = use Ollama; >0 = a trained/BYO model
    # Object detection (bounding boxes: where + what). Detection datasets carry
    # per-sample boxes; runs fine-tune a YOLO *detect* model from this base.
    detect_base_model: str = "yolo11n.pt"  # downloaded on first detection train
    detect_image_size: int = 640  # detection wants larger frames than cls
    detect_conf: float = 0.35  # min box confidence reported by the live detector


@dataclass
class PluginsConfig:
    # Plugin system. Plugins extend TailCam with extra AI analyzer providers and
    # notification channels, discovered via Python entry points (pip-installed)
    # and a drop-in folder. ``disabled`` lists plugin ids to skip; ``load_dropins``
    # toggles loading single-file plugins from the config dir's ``plugins/`` folder.
    disabled: list[str] = field(default_factory=list)
    load_dropins: bool = True


@dataclass
class NotificationsConfig:
    # Push alerts to Discord, Telegram, and/or a generic webhook (the latter is
    # the route for a personal bot like Hermes/OpenClaw — TailCam POSTs a JSON
    # event your bot can filter and forward). All channels are independent.
    enabled: bool = False
    discord_webhook: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    webhook_url: str = ""  # generic JSON webhook (your bot, n8n, etc.)
    # Which events fire a notification.
    notify_motion: bool = True
    notify_camera_offline: bool = True
    notify_training: bool = True
    # Motion filters, so you're not spammed.
    min_confidence: float = 0.0  # 0..1; skip AI labels below this
    labels: list[str] = field(default_factory=list)  # allowlist; empty = all labels
    cooldown_seconds: float = 60.0  # per-camera quiet period between motion alerts


@dataclass
class MCPConfig:
    # Model Context Protocol server: exposes cameras, events, media, health, and
    # admin workflows to agents (Codex, Claude, OpenClaw/Hermes). ``tailcam mcp
    # stdio`` is always available for local clients; the network ``/mcp`` mount is
    # served only when both ``enabled`` and ``http_enabled`` are true and is
    # gated by the same Tailscale identity/role checks as the v1 management API.
    enabled: bool = True
    http_enabled: bool = False
    instructions_profile: str = "personal"  # personal | fleet
    max_events: int = 100
    max_media: int = 100
    allow_image_content: bool = True
    require_confirm_for_writes: bool = True
    require_confirm_for_fleet_writes: bool = True


@dataclass
class HomeKitConfig:
    # Expose cameras to Apple Home via HAP (HomeKit Accessory Protocol) — the
    # native, working path for live camera video in the Home app on iPhone/iPad/
    # Mac. (Matter does not yet carry camera streams to Apple Home, so HomeKit
    # cameras use HAP directly — no Matter bridge required.) Requires the
    # ``homekit`` extra (HAP-python) and ``ffmpeg`` on the host for live video.
    enabled: bool = False
    bridge_name: str = "TailCam"
    # HomeKit setup code shown for pairing, format ``XXX-XX-XXX``. A random valid
    # code is generated on first enable if left at the placeholder.
    pin: str = ""
    port: int = 51826
    # Camera ids to expose; empty = all cameras.
    cameras: list[str] = field(default_factory=list)
    # ffmpeg binary used to transcode TailCam's MJPEG into HomeKit's H.264/SRTP.
    ffmpeg: str = "ffmpeg"


@dataclass
class HomeAssistantConfig:
    # Home Assistant integration. Cameras are added to HA natively via the
    # built-in MJPEG IP Camera integration (TailCam already serves the stream +
    # snapshot URLs) — see the Integrations panel for ready-to-paste config.
    # Optionally, MQTT discovery publishes motion + connectivity as binary
    # sensors so HA automations can react to TailCam events (needs the ``mqtt``
    # extra, paho-mqtt, and a broker HA also listens to).
    enabled: bool = False
    mqtt_host: str = ""  # broker host; empty disables MQTT discovery
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_tls: bool = False
    discovery_prefix: str = "homeassistant"  # HA MQTT discovery prefix
    node_id: str = "tailcam"  # namespaces this node's MQTT topics
    publish_motion: bool = True
    publish_status: bool = True
    motion_reset_seconds: float = 20.0  # auto-clear a motion sensor after this


# Bumped when older config files need a one-time value migration on load (see
# ``AppConfig.load``). Version 2: motion.auto_record flipped to default-on.
_CONFIG_VERSION = 2

_T = TypeVar("_T")


def _section(dc: type[_T], raw: dict[str, Any], key: str) -> _T:
    """Build one config section, ignoring unknown keys.

    A config written by a newer TailCam (or hand-edited with a typo) must not
    TypeError and reset the ENTIRE file to defaults — unknown keys are simply
    dropped and known ones kept.
    """
    data = raw.get(key) or {}
    if not isinstance(data, dict):
        return dc()
    names = {f.name for f in fields(dc)}  # type: ignore[arg-type]
    return dc(**{k: v for k, v in data.items() if k in names})


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    tailscale: TailscaleConfig = field(default_factory=TailscaleConfig)
    peers: PeersConfig = field(default_factory=PeersConfig)
    cameras: CamerasConfig = field(default_factory=CamerasConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    timelapse: TimelapseConfig = field(default_factory=TimelapseConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    homekit: HomeKitConfig = field(default_factory=HomeKitConfig)
    homeassistant: HomeAssistantConfig = field(default_factory=HomeAssistantConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        cfg_path = path or paths.config_file()
        config: AppConfig
        if not cfg_path.exists():
            config = cls()
        else:
            try:
                with cfg_path.open("rb") as fh:
                    raw = tomllib.load(fh)
                config = cls.from_dict(raw)
            except (tomllib.TOMLDecodeError, OSError, TypeError, ValueError) as exc:
                # A malformed/hand-edited config must NOT brick every command or
                # crash-loop the background service. Back the bad file up and run on
                # defaults; the user can fix it and `tailcam restart`.
                logging.getLogger("tailcam.config").error(
                    "Invalid config at %s (%s). Using defaults; bad file saved as %s.bad",
                    cfg_path, exc, cfg_path.name,
                )
                try:
                    cfg_path.replace(cfg_path.with_suffix(cfg_path.suffix + ".bad"))
                except OSError:
                    pass
                config = cls()
        # Loading config is the single choke point every entry path goes
        # through (server, CLI, MCP), so apply the custom media location here —
        # before anything computes or creates media paths.
        paths.set_media_override(config.storage.media_dir)
        return config

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AppConfig:
        config = cls(
            server=_section(ServerConfig, raw, "server"),
            stream=_section(StreamConfig, raw, "stream"),
            motion=_section(MotionConfig, raw, "motion"),
            retention=_section(RetentionConfig, raw, "retention"),
            storage=_section(StorageConfig, raw, "storage"),
            tailscale=_section(TailscaleConfig, raw, "tailscale"),
            peers=_section(PeersConfig, raw, "peers"),
            cameras=_section(CamerasConfig, raw, "cameras"),
            detection=_section(DetectionConfig, raw, "detection"),
            ai=_section(AIConfig, raw, "ai"),
            timelapse=_section(TimelapseConfig, raw, "timelapse"),
            training=_section(TrainingConfig, raw, "training"),
            plugins=_section(PluginsConfig, raw, "plugins"),
            notifications=_section(NotificationsConfig, raw, "notifications"),
            homekit=_section(HomeKitConfig, raw, "homekit"),
            homeassistant=_section(HomeAssistantConfig, raw, "homeassistant"),
            mcp=_section(MCPConfig, raw, "mcp"),
        )
        # One-time value migrations for files written by older versions. A file
        # saved by this version records _CONFIG_VERSION, so a value the user
        # sets afterwards is never touched again.
        version = int(raw.get("config_version", 1) or 1)
        if version < 2:
            # v2 (0.99.11): motion clips became opt-out. Older files all carry
            # auto_record=false because that was the *default*, not a choice —
            # every motion event showed "no clip" and nothing was recorded.
            config.motion.auto_record = True
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            # Top-level scalars must precede tables in TOML; keep this first.
            "config_version": _CONFIG_VERSION,
            "server": asdict(self.server),
            "stream": asdict(self.stream),
            "motion": asdict(self.motion),
            "retention": asdict(self.retention),
            "storage": asdict(self.storage),
            "tailscale": asdict(self.tailscale),
            "peers": asdict(self.peers),
            "cameras": asdict(self.cameras),
            "detection": asdict(self.detection),
            "ai": asdict(self.ai),
            "timelapse": asdict(self.timelapse),
            "training": asdict(self.training),
            "plugins": asdict(self.plugins),
            "notifications": asdict(self.notifications),
            "homekit": asdict(self.homekit),
            "homeassistant": asdict(self.homeassistant),
            "mcp": asdict(self.mcp),
        }

    def save(self, path: Path | None = None) -> None:
        cfg_path = path or paths.config_file()
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg_path.open("wb") as fh:
            tomli_w.dump(self.to_dict(), fh)
