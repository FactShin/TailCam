"""Application configuration backed by a human-editable TOML file.

App-level and per-camera *display* settings live here. Dynamic, queryable data
(camera registry, media index, motion events) lives in SQLite instead.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

import tomli_w

from anycam import paths


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
    # Tailnet-facing HTTPS port. 8443 keeps AnyCam off the root URL (443) so it
    # won't clobber another app already served there. Tailscale allows 443,
    # 8443, and 10000 for serve/funnel.
    serve_port: int = 8443


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    tailscale: TailscaleConfig = field(default_factory=TailscaleConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        cfg_path = path or paths.config_file()
        if not cfg_path.exists():
            return cls()
        with cfg_path.open("rb") as fh:
            raw = tomllib.load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AppConfig:
        return cls(
            server=ServerConfig(**raw.get("server", {})),
            stream=StreamConfig(**raw.get("stream", {})),
            motion=MotionConfig(**raw.get("motion", {})),
            retention=RetentionConfig(**raw.get("retention", {})),
            tailscale=TailscaleConfig(**raw.get("tailscale", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": asdict(self.server),
            "stream": asdict(self.stream),
            "motion": asdict(self.motion),
            "retention": asdict(self.retention),
            "tailscale": asdict(self.tailscale),
        }

    def save(self, path: Path | None = None) -> None:
        cfg_path = path or paths.config_file()
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg_path.open("wb") as fh:
            tomli_w.dump(self.to_dict(), fh)
