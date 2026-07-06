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
    smooth_engine: str = ""  # "ffmpeg" | "rife" — which engine produced it
    # Immutable per-capture printer settings.
    jpeg_quality: int = 90
    max_frames: int = 0
    auto_smooth: bool = False
    smooth_target_fps: int = 60
    smooth_interpolate: bool = True
    smooth_deflicker: bool = True
    smooth_quality: str = "high"
    analysis_enabled: bool = False
    analysis_cadence_seconds: float = 60.0


@dataclass
class TimelapseAnalysisEventRecord:
    id: int | None
    timelapse_id: int
    frame_number: int
    state: str
    confidence: float
    description: str
    evidence_path: str
    created_ts: float


# -- model training ---------------------------------------------------------


@dataclass
class DatasetRecord:
    id: int | None
    name: str
    task: str  # "classification" | "detection"
    created_ts: float
    note: str = ""
    # Bumped every time an active-learning sync lands new human annotations, so
    # a trained model can be traced back to the dataset state it saw.
    version: int = 1


@dataclass
class DatasetSampleRecord:
    id: int | None
    dataset_id: int
    path: str
    thumb: str | None
    label: str | None  # None = unlabeled (awaiting a human/auto label)
    source: str  # "collect" | "motion" | "timelapse" | "manual"
    camera_id: str
    host: str
    created_ts: float
    confidence: float | None = None  # weak-label confidence, if auto-labeled


@dataclass
class SampleAnnotationRecord:
    """One bounding box on a detection sample. Coordinates are normalized to the
    image (0..1): (cx, cy) is the box center, (w, h) its size — the layout YOLO
    detection labels use, so export is a direct write."""

    id: int | None
    sample_id: int
    label: str
    cx: float
    cy: float
    w: float
    h: float
    created_ts: float


@dataclass
class ReviewItemRecord:
    """One frame sent to Label Studio for human review (active learning).

    Ties a dataset sample to its Label Studio task so completed annotations can
    be pulled back and written onto the right sample, and records the labeling
    provenance the pipeline needs (which model pre-labeled, how confident)."""

    id: int | None
    sample_id: int
    dataset_id: int
    ls_project_id: int
    ls_task_id: int
    # pending | completed | error
    status: str
    labeling_model: str  # backend id that pre-labeled the frame
    confidence: float | None  # lowest prediction confidence that triggered review
    created_ts: float
    completed_ts: float | None = None


@dataclass
class ModelRecord:
    id: int | None
    name: str
    kind: str  # "base" | "trained" | "byo"
    path: str  # artifact path ("" for a not-yet-downloaded base)
    classes_json: str  # JSON list of class names
    base_model: str  # what it was fine-tuned from
    metrics_json: str  # JSON dict of training metrics
    created_ts: float
    active: int = 0  # 1 = the model used for inference
    task: str = "classification"  # "classification" | "detection"


@dataclass
class TrainingRunRecord:
    id: int | None
    dataset_id: int
    model_id: int | None  # the produced model, once complete
    base_model: str
    # queued | preparing | training | complete | error | stopped
    status: str
    params_json: str
    metrics_json: str
    log: str
    epochs: int
    epoch: int  # current epoch (progress)
    created_ts: float
    started_ts: float | None = None
    ended_ts: float | None = None


@dataclass
class AuditRecord:
    id: int | None
    created_ts: float
    actor: str
    source: str
    action: str
    target: str
    result: str
    detail: str | None
    metadata_json: str = "{}"
