// Mirrors TailCam's FastAPI schemas (src/tailcam/web/schemas.py).

export type CameraStatus = "online" | "degraded" | "offline";

export interface Transform {
  rotation: number; // 0 | 90 | 180 | 270
  flip_h: boolean;
  flip_v: boolean;
}

export interface CameraInfo {
  id: string;
  name: string;
  backend: string; // "v4l2" | "avfoundation" | "synthetic"
  status: CameraStatus;
  fps: number;
  width: number;
  height: number;
  recording: boolean;
  motion_enabled: boolean;
  properties: Record<string, number | null>;
  transform: Transform;
  // Why the camera is offline/degraded, if the server knows (e.g. permissions).
  last_error: string | null;
  // Multi-host: which node owns this camera, and the prefix to reach its
  // stream/controls through the node you're viewing ("" = local).
  host: string;
  proxy_prefix: string;
}

export interface HostInfo {
  host: string;
  kind: "local" | "peer";
  online: boolean;
  version: string | null;
  camera_count: number;
  proxy_prefix: string;
}

export interface CameraSettingsUpdate {
  name?: string;
  properties?: {
    width?: number;
    height?: number;
    fps?: number;
    brightness?: number;
    contrast?: number;
    saturation?: number;
  };
  transform?: Transform;
  motion_enabled?: boolean;
}

export interface MediaInfo {
  id: number;
  camera_id: string;
  media_type: "snapshot" | "recording";
  created_ts: number;
  trigger: "manual" | "motion";
  size_bytes: number;
  has_thumbnail: boolean;
  host: string;
  proxy_prefix: string;
}

export interface MotionEventInfo {
  id: number;
  camera_id: string;
  start_ts: number;
  end_ts: number | null;
  peak_score: number;
  recording_id: number | null;
  label: string | null;
  description: string | null;
  confidence: number | null;
  has_thumb: boolean;
  host: string;
  proxy_prefix: string;
}

export interface AIInfo {
  enabled: boolean;
  reachable: boolean;
  model: string;
  model_present: boolean;
  base_url: string;
}

export interface AIUpdate {
  enabled?: boolean;
  model?: string;
  base_url?: string;
}

export interface OllamaModelsInfo {
  reachable: boolean;
  base_url: string;
  active_model: string;
  installed: string[];
}

export interface AIPullStatus {
  model: string;
  active: boolean;
  status: "idle" | "pulling" | "success" | "error";
  completed: number;
  total: number;
  percent: number;
  detail: string;
  error: string | null;
}

// -- plugins --

export interface PluginEntry {
  id: string;
  name: string;
  kind: string;
  description: string;
  version: string;
  builtin: boolean;
}

export interface PluginsInfo {
  plugins: PluginEntry[];
  analyzer_providers: { id: string; name: string; description: string }[];
  notification_channels: { id: string; name: string }[];
  active_provider: string;
  errors: string[];
}

// -- notifications --

export interface NotificationsInfo {
  enabled: boolean;
  discord_webhook: string;
  telegram_token: string;
  telegram_chat_id: string;
  webhook_url: string;
  notify_motion: boolean;
  notify_camera_offline: boolean;
  notify_training: boolean;
  min_confidence: number;
  labels: string[];
  cooldown_seconds: number;
  channels: string[];
}

export type NotificationsUpdate = Partial<Omit<NotificationsInfo, "channels">>;

// -- model training --

export interface TrainingInfo {
  engine_available: boolean;
  framework: string;
  version: string | null;
  device: string;
  collecting: boolean;
  collect_enabled: boolean;
  collect_interval_seconds: number;
  auto_label: boolean;
  active_dataset_id: number;
  active_model_id: number;
  classes: string[];
  total_samples: number;
  dataset_count: number;
  model_count: number;
  collected_session: number;
}

export interface CollectionUpdate {
  enabled?: boolean;
  interval_seconds?: number;
  auto_label?: boolean;
  active_dataset_id?: number;
}

export type DatasetTask = "classification" | "detection";

export interface DatasetInfo {
  id: number;
  name: string;
  task: DatasetTask;
  created_ts: number;
  note: string;
  sample_count: number;
  label_counts: Record<string, number>;
  annotated_count: number;
  box_label_counts: Record<string, number>;
}

export interface SampleInfo {
  id: number;
  dataset_id: number;
  label: string | null;
  source: string;
  camera_id: string;
  host: string;
  created_ts: number;
  confidence: number | null;
  has_thumb: boolean;
  annotation_count: number;
}

// A bounding box, normalized 0..1 (center + size) — matches the backend.
export interface AnnotationBox {
  label: string;
  cx: number;
  cy: number;
  w: number;
  h: number;
}

export interface SampleAnnotations {
  sample_id: number;
  boxes: AnnotationBox[];
}

export interface DetectionBox extends AnnotationBox {
  confidence: number;
}

export interface DetectionResult {
  camera_id: string;
  detector_active: boolean;
  model_name: string | null;
  boxes: DetectionBox[];
}

export interface ModelInfo {
  id: number;
  name: string;
  kind: "base" | "trained" | "byo";
  task: DatasetTask;
  active: boolean;
  base_model: string;
  classes: string[];
  metrics: Record<string, unknown>;
  created_ts: number;
  has_artifact: boolean;
}

export type RunStatus = "queued" | "preparing" | "training" | "complete" | "error" | "stopped";

export interface TrainingRunInfo {
  id: number;
  dataset_id: number;
  model_id: number | null;
  base_model: string;
  status: RunStatus;
  epochs: number;
  epoch: number;
  metrics: Record<string, number>;
  log: string;
  created_ts: number;
  started_ts: number | null;
  ended_ts: number | null;
}

export type TimelapseState = "capturing" | "encoding" | "complete" | "interrupted" | "error";

export interface TimelapseInfo {
  id: number;
  camera_id: string;
  name: string;
  state: TimelapseState;
  mode: string; // "interval" (future: "layer")
  interval_seconds: number;
  output_fps: number;
  frames_captured: number;
  created_ts: number;
  start_ts: number;
  end_ts: number | null;
  size_bytes: number;
  width: number;
  height: number;
  has_video: boolean;
  has_thumb: boolean;
  smooth_state: "none" | "processing" | "complete" | "error";
  has_smooth: boolean;
  smooth_size_bytes: number;
  smooth_engine: string; // "ffmpeg" | "rife"
  jpeg_quality: number;
  max_frames: number;
  auto_smooth: boolean;
  smooth_target_fps: number;
  smooth_interpolate: boolean;
  smooth_deflicker: boolean;
  smooth_quality: "standard" | "high" | "maximum";
  analysis_enabled: boolean;
  analysis_cadence_seconds: number;
  analysis_event_count: number;
  analysis_latest_state: string;
  host: string;
  proxy_prefix: string;
}

export interface TimelapseStartParams {
  name?: string;
  interval_seconds?: number;
  output_fps?: number;
  duration_seconds?: number;
  jpeg_quality?: number;
  max_frames?: number;
  auto_smooth?: boolean;
  smooth_target_fps?: number;
  smooth_interpolate?: boolean;
  smooth_deflicker?: boolean;
  smooth_engine?: "ffmpeg" | "rife";
  smooth_quality?: "standard" | "high" | "maximum";
  analysis_enabled?: boolean;
  analysis_cadence_seconds?: number;
}

export interface TimelapseSmoothParams {
  target_fps?: number;
  interpolate?: boolean;
  deflicker?: boolean;
  engine?: string; // "ffmpeg" | "rife"
  quality?: "standard" | "high" | "maximum";
}

export interface TimelapsePreset {
  name: string;
  settings: TimelapseStartParams;
}

export interface TimelapseAnalysisEvent {
  id: number;
  timelapse_id: number;
  frame_number: number;
  state: "healthy" | "possible_failure" | "failure" | "uncertain";
  confidence: number;
  description: string;
  created_ts: number;
}

export interface EngineInfo {
  id: "ffmpeg" | "rife";
  label: string;
  available: boolean;
  source: "system" | "bundled" | "missing";
  version: string | null;
}

export interface PostprocessInfo {
  available: boolean;
  default_engine: "ffmpeg" | "rife";
  default_target_fps: number;
  engines: EngineInfo[];
}

export interface SystemInfo {
  version: string;
  host: string;
  tailscale_installed: boolean;
  tailscale_running: boolean;
  access_url: string;
  local_url: string;
  media_bytes: number;
  hidden_count: number;
}

// Per-tab view params for the MJPEG stream (local to each browser).
export interface ViewParams {
  fps: number;
  zoom: number;
  panX: number;
  panY: number;
  quality: number;
  w: number;
}

export const VIEW_DEFAULT: ViewParams = {
  fps: 15,
  zoom: 1,
  panX: 0.5,
  panY: 0.5,
  quality: 75,
  w: 0,
};
