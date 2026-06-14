"""Application configuration backed by a human-editable TOML file.

App-level and per-camera *display* settings live here. Dynamic, queryable data
(camera registry, media index, motion events) lives in SQLite instead.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
    auto_record: bool = False
    record_tail_seconds: float = 5.0


@dataclass
class RetentionConfig:
    max_gb: float = 10.0
    max_age_days: int = 30


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
    # Post-processing ("Smooth"): motion-interpolate the captured frames up to
    # smooth_target_fps for flowing motion, and even out exposure flicker.
    smooth_target_fps: int = 60
    smooth_interpolate: bool = True
    smooth_deflicker: bool = True


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    tailscale: TailscaleConfig = field(default_factory=TailscaleConfig)
    peers: PeersConfig = field(default_factory=PeersConfig)
    cameras: CamerasConfig = field(default_factory=CamerasConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    timelapse: TimelapseConfig = field(default_factory=TimelapseConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        cfg_path = path or paths.config_file()
        if not cfg_path.exists():
            return cls()
        try:
            with cfg_path.open("rb") as fh:
                raw = tomllib.load(fh)
            return cls.from_dict(raw)
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
            return cls()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AppConfig:
        return cls(
            server=ServerConfig(**raw.get("server", {})),
            stream=StreamConfig(**raw.get("stream", {})),
            motion=MotionConfig(**raw.get("motion", {})),
            retention=RetentionConfig(**raw.get("retention", {})),
            tailscale=TailscaleConfig(**raw.get("tailscale", {})),
            peers=PeersConfig(**raw.get("peers", {})),
            cameras=CamerasConfig(**raw.get("cameras", {})),
            ai=AIConfig(**raw.get("ai", {})),
            timelapse=TimelapseConfig(**raw.get("timelapse", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": asdict(self.server),
            "stream": asdict(self.stream),
            "motion": asdict(self.motion),
            "retention": asdict(self.retention),
            "tailscale": asdict(self.tailscale),
            "peers": asdict(self.peers),
            "cameras": asdict(self.cameras),
            "ai": asdict(self.ai),
            "timelapse": asdict(self.timelapse),
        }

    def save(self, path: Path | None = None) -> None:
        cfg_path = path or paths.config_file()
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg_path.open("wb") as fh:
            tomli_w.dump(self.to_dict(), fh)
