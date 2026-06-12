"""Row dataclasses for persisted records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CameraRecord:
    id: str
    name: str
    backend: str
    settings_json: str
    last_seen: float


@dataclass
class MediaRecord:
    id: int | None
    camera_id: str
    media_type: str  # "snapshot" | "recording"
    path: str
    thumbnail: str | None
    created_ts: float
    trigger: str  # "manual" | "motion"
    size_bytes: int


@dataclass
class MotionEventRecord:
    id: int | None
    camera_id: str
    start_ts: float
    end_ts: float | None
    peak_score: float
    recording_id: int | None
    label: str | None = None
    description: str | None = None
    confidence: float | None = None
    thumb_path: str | None = None
