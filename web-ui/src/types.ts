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
  host: string;
  proxy_prefix: string;
}

export interface TimelapseStartParams {
  name?: string;
  interval_seconds?: number;
  output_fps?: number;
  duration_seconds?: number;
}

export interface TimelapseSmoothParams {
  target_fps?: number;
  interpolate?: boolean;
  deflicker?: boolean;
  engine?: string; // "ffmpeg" | "rife"
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
