// Mirrors AnyCam's FastAPI schemas (src/anycam/web/schemas.py).

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
}

export interface MotionEventInfo {
  id: number;
  camera_id: string;
  start_ts: number;
  end_ts: number | null;
  peak_score: number;
  recording_id: number | null;
}

export interface SystemInfo {
  version: string;
  host: string;
  tailscale_installed: boolean;
  tailscale_running: boolean;
  access_url: string;
  local_url: string;
  media_bytes: number;
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
