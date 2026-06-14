// React Query hooks. Polling intervals pause automatically on hidden tabs
// (refetchIntervalInBackground defaults to false).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import * as api from "./client";
import type { CameraInfo, CameraSettingsUpdate, TimelapseStartParams } from "../types";

export function useCameras() {
  return useQuery({ queryKey: ["cameras"], queryFn: api.getCameras, refetchInterval: 2500 });
}

export function useCamera(prefix: string, id: string) {
  return useQuery({
    queryKey: ["camera", prefix, id],
    queryFn: () => api.getCamera(prefix, id),
    refetchInterval: 2500,
  });
}

export function useHosts() {
  return useQuery({ queryKey: ["hosts"], queryFn: api.getHosts, refetchInterval: 4000 });
}

export function useSystem() {
  return useQuery({ queryKey: ["system"], queryFn: api.getSystem, refetchInterval: 15000 });
}

export function useMedia(params: { camera_id?: string; media_type?: string; limit?: number }) {
  return useQuery({
    queryKey: ["media", params],
    queryFn: () => api.getMedia(params),
  });
}

export function useEvents(params: { camera_id?: string; limit?: number }) {
  return useQuery({
    queryKey: ["events", params],
    queryFn: () => api.getEvents(params),
    refetchInterval: 5000,
  });
}

export function useUpdate() {
  return useQuery({ queryKey: ["update"], queryFn: api.getUpdate, refetchInterval: 3600_000 });
}

export function useAi() {
  return useQuery({ queryKey: ["ai"], queryFn: api.getAi, refetchInterval: 30_000 });
}

export function useRefreshCameras() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.refreshCameras,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cameras"] });
      qc.invalidateQueries({ queryKey: ["hosts"] });
    },
  });
}

function _invalidateCamerasHosts(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["cameras"] });
  qc.invalidateQueries({ queryKey: ["hosts"] });
  qc.invalidateQueries({ queryKey: ["system"] });
}

export function useReload() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.reloadSystem, onSuccess: () => _invalidateCamerasHosts(qc) });
}

export function useRestoreHidden() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.restoreHidden, onSuccess: () => _invalidateCamerasHosts(qc) });
}

export function useRestartCamera() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id }: { prefix: string; id: string }) => api.restartCamera(prefix, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
  });
}

export function useDeleteCamera() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id }: { prefix: string; id: string }) => api.deleteCamera(prefix, id),
    onSuccess: () => _invalidateCamerasHosts(qc),
  });
}

export function usePatchCamera(prefix: string, id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (update: CameraSettingsUpdate) => api.patchCamera(prefix, id, update),
    onMutate: async (update) => {
      await qc.cancelQueries({ queryKey: ["cameras"] });
      const prev = qc.getQueryData<CameraInfo[]>(["cameras"]);
      if (prev) {
        qc.setQueryData<CameraInfo[]>(
          ["cameras"],
          prev.map((c) =>
            c.id === id && c.proxy_prefix === prefix ? applyOptimistic(c, update) : c,
          ),
        );
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["cameras"], ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["cameras"] });
      qc.invalidateQueries({ queryKey: ["camera", prefix, id] });
    },
  });
}

function applyOptimistic(cam: CameraInfo, u: CameraSettingsUpdate): CameraInfo {
  const next: CameraInfo = { ...cam };
  if (u.name !== undefined) next.name = u.name;
  if (u.motion_enabled !== undefined) next.motion_enabled = u.motion_enabled;
  if (u.transform) next.transform = { ...next.transform, ...u.transform };
  if (u.properties) {
    next.properties = { ...next.properties, ...u.properties };
    if (u.properties.width) next.width = u.properties.width;
    if (u.properties.height) next.height = u.properties.height;
  }
  return next;
}

export function useSnapshot(prefix: string, id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.postSnapshot(prefix, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["media"] }),
  });
}

export function useRecording(prefix: string, id: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["cameras"] });
    qc.invalidateQueries({ queryKey: ["media"] });
  };
  return {
    start: useMutation({ mutationFn: () => api.startRecording(prefix, id), onSuccess: invalidate }),
    stop: useMutation({ mutationFn: () => api.stopRecording(prefix, id), onSuccess: invalidate }),
  };
}

export function useDeleteMedia() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id }: { prefix: string; id: number }) => api.deleteMedia(prefix, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["media"] });
      qc.invalidateQueries({ queryKey: ["system"] });
    },
  });
}

// -- timelapse ---------------------------------------------------------------

export function useTimelapses(params: { camera_id?: string; limit?: number } = {}) {
  return useQuery({
    queryKey: ["timelapse", params],
    queryFn: () => api.getTimelapses(params),
    refetchInterval: 3000, // reflect capture progress + encode → complete
  });
}

function _invalidateTimelapse(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["timelapse"] });
  qc.invalidateQueries({ queryKey: ["system"] });
}

export function useStartTimelapse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id, params }: { prefix: string; id: string; params: TimelapseStartParams }) =>
      api.startTimelapse(prefix, id, params),
    onSuccess: () => _invalidateTimelapse(qc),
  });
}

export function useStopTimelapse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id }: { prefix: string; id: number }) => api.stopTimelapse(prefix, id),
    onSuccess: () => _invalidateTimelapse(qc),
  });
}

export function useEncodeTimelapse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id }: { prefix: string; id: number }) => api.encodeTimelapse(prefix, id),
    onSuccess: () => _invalidateTimelapse(qc),
  });
}

export function useDeleteTimelapse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id }: { prefix: string; id: number }) => api.deleteTimelapse(prefix, id),
    onSuccess: () => _invalidateTimelapse(qc),
  });
}

// Pause live streams when the tab is hidden.
export function usePageVisible() {
  const [vis, setVis] = useState(!document.hidden);
  useEffect(() => {
    const on = () => setVis(!document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return vis;
}
