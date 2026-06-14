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


@dataclass
class TimelapseRecord:
    id: int | None
    camera_id: str
    name: str
    # capturing | encoding | complete | interrupted | error
    state: str
    # "interval" today; "layer" once printer (Moonraker/OctoPrint) sync lands.
    mode: str
    interval_seconds: float
    output_fps: int
    frames_captured: int
    created_ts: float
    start_ts: float
    end_ts: float | None
    # Raw captured JPEGs are kept (not just the encoded mp4) so post-processing
    # — frame interpolation, deflicker — can re-stitch them into smooth motion.
    frames_dir: str
    video_path: str | None = None
    thumb_path: str | None = None
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    # Smoothed/interpolated variant (ffmpeg post-processing). The raw frames are
    # kept, so this can be (re)generated at any time without re-capturing.
    smooth_state: str = "none"  # none | processing | complete | error
    smooth_path: str | None = None
    smooth_size_bytes: int = 0

