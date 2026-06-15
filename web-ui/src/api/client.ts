// Same-origin fetch client + URL builders for TailCam.
//
// Camera ids may contain slashes (e.g. "/dev/video0"); we never url-encode the
// slashes — the backend uses a path matcher. Remote cameras carry a
// `proxy_prefix` ("/proxy/<key>") that must be prepended to every URL so the
// request is reverse-proxied to the node that owns the camera.

import type {
  CameraInfo,
  CameraSettingsUpdate,
  HostInfo,
  MediaInfo,
  MotionEventInfo,
  PostprocessInfo,
  SystemInfo,
  TimelapseInfo,
  TimelapseSmoothParams,
  TimelapseStartParams,
  ViewParams,
} from "../types";

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const qs = (params: Record<string, string | number | undefined>) => {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}=${v}`);
  return parts.length ? `?${parts.join("&")}` : "";
};

// ---- URL builders (used in <img>/<video>/<a>, prefixed for remote hosts) ----

export function streamUrl(prefix: string, id: string, v: ViewParams): string {
  const params: Record<string, string | number | undefined> = {
    fps: Math.round(v.fps),
    zoom: v.zoom > 1 ? v.zoom.toFixed(1) : undefined,
    pan_x: v.zoom > 1 ? v.panX.toFixed(2) : undefined,
    pan_y: v.zoom > 1 ? v.panY.toFixed(2) : undefined,
    w: v.w || undefined,
    q: v.quality,
  };
  return `${prefix}/stream/${id}.mjpg${qs(params)}`;
}

export const snapshotUrl = (prefix: string, id: string) => `${prefix}/stream/${id}/snapshot.jpg`;
export const mediaFileUrl = (prefix: string, mid: number) => `${prefix}/media/${mid}/file`;
export const mediaThumbUrl = (prefix: string, mid: number) => `${prefix}/media/${mid}/thumbnail`;
export const cacheBust = () => `_=${Date.now()}`;

// ---- REST ----

export const getCameras = () => jsonFetch<CameraInfo[]>("/api/cameras");
export const getCamera = (prefix: string, id: string) =>
  jsonFetch<CameraInfo>(`${prefix}/api/cameras/${id}`);
export const refreshCameras = () =>
  jsonFetch<CameraInfo[]>("/api/cameras/refresh", { method: "POST" });
export const getHosts = () => jsonFetch<HostInfo[]>("/api/hosts");
export const getSystem = () => jsonFetch<SystemInfo>("/api/system");
export const getUpdate = () =>
  jsonFetch<{ current: string; latest: string | null; available: boolean }>("/api/update");
export const getAi = () => jsonFetch<import("../types").AIInfo>("/api/ai");
export const updateAi = (body: import("../types").AIUpdate) =>
  jsonFetch<import("../types").AIInfo>("/api/ai", { method: "POST", body: JSON.stringify(body) });
export const eventThumbUrl = (prefix: string, eventId: number) =>
  `${prefix}/events/${eventId}/thumbnail`;
export const reloadSystem = () =>
  jsonFetch<CameraInfo[]>("/api/system/reload", { method: "POST" });
export const restoreHidden = () =>
  jsonFetch<CameraInfo[]>("/api/cameras/restore-hidden", { method: "POST" });

export const restartCamera = (prefix: string, id: string) =>
  jsonFetch<{ ok: boolean }>(`${prefix}/api/cameras/${id}/restart`, { method: "POST" });
export const deleteCamera = (prefix: string, id: string) =>
  jsonFetch<{ ok: boolean }>(`${prefix}/api/cameras/${id}`, { method: "DELETE" });

export const patchCamera = (prefix: string, id: string, update: CameraSettingsUpdate) =>
  jsonFetch<CameraInfo>(`${prefix}/api/cameras/${id}`, {
    method: "PATCH",
    body: JSON.stringify(update),
  });

export const postSnapshot = (prefix: string, id: string) =>
  jsonFetch<{ ok: boolean; media_id: number | null }>(`${prefix}/api/cameras/${id}/snapshot`, {
    method: "POST",
  });

export const startRecording = (prefix: string, id: string) =>
  jsonFetch<{ ok: boolean }>(`${prefix}/api/cameras/${id}/recording/start`, { method: "POST" });

export const stopRecording = (prefix: string, id: string) =>
  jsonFetch<{ ok: boolean; media_id: number | null }>(
    `${prefix}/api/cameras/${id}/recording/stop`,
    { method: "POST" },
  );

export const getMedia = (params: { camera_id?: string; media_type?: string; limit?: number }) =>
  jsonFetch<MediaInfo[]>(
    `/api/media${qs({
      camera_id: params.camera_id,
      media_type: params.media_type,
      limit: params.limit ?? 50,
    })}`,
  );

export const deleteMedia = (prefix: string, mid: number) =>
  jsonFetch<{ ok: boolean }>(`${prefix}/api/media/${mid}`, { method: "DELETE" });

export const getEvents = (params: { camera_id?: string; limit?: number }) =>
  jsonFetch<MotionEventInfo[]>(
    `/api/events${qs({ camera_id: params.camera_id, limit: params.limit ?? 50 })}`,
  );

// ---- timelapse ----

export const getTimelapses = (params: { camera_id?: string; limit?: number } = {}) =>
  jsonFetch<TimelapseInfo[]>(
    `/api/timelapse${qs({ camera_id: params.camera_id, limit: params.limit ?? 100 })}`,
  );

export const startTimelapse = (prefix: string, id: string, body: TimelapseStartParams) =>
  jsonFetch<TimelapseInfo>(`${prefix}/api/cameras/${id}/timelapse/start`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const stopTimelapse = (prefix: string, tlId: number) =>
  jsonFetch<TimelapseInfo>(`${prefix}/api/timelapse/${tlId}/stop`, { method: "POST" });

export const encodeTimelapse = (prefix: string, tlId: number) =>
  jsonFetch<TimelapseInfo>(`${prefix}/api/timelapse/${tlId}/encode`, { method: "POST" });

export const deleteTimelapse = (prefix: string, tlId: number) =>
  jsonFetch<{ ok: boolean }>(`${prefix}/api/timelapse/${tlId}`, { method: "DELETE" });

export const smoothTimelapse = (prefix: string, tlId: number, body: TimelapseSmoothParams = {}) =>
  jsonFetch<TimelapseInfo>(`${prefix}/api/timelapse/${tlId}/smooth`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getPostprocess = () => jsonFetch<PostprocessInfo>("/api/postprocess");

export const setPostprocess = (body: { default_engine?: string }) =>
  jsonFetch<PostprocessInfo>("/api/postprocess", { method: "POST", body: JSON.stringify(body) });

export const timelapseFileUrl = (prefix: string, tlId: number) => `${prefix}/timelapse/${tlId}/file`;
export const timelapseSmoothUrl = (prefix: string, tlId: number) =>
  `${prefix}/timelapse/${tlId}/smooth`;
export const timelapseThumbUrl = (prefix: string, tlId: number) =>
  `${prefix}/timelapse/${tlId}/thumbnail`;
