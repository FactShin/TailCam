// React Query hooks. Polling intervals pause automatically on hidden tabs
// (refetchIntervalInBackground defaults to false).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import * as api from "./client";
import type {
  CameraInfo,
  CameraSettingsUpdate,
  CollectionUpdate,
  TimelapseSmoothParams,
  TimelapseStartParams,
} from "../types";

export function useCameras() {
  return useQuery({ queryKey: ["cameras"], queryFn: api.getCameras, refetchInterval: 2500 });
}

// -- model training --

function _invTraining(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["training"] });
  qc.invalidateQueries({ queryKey: ["datasets"] });
  qc.invalidateQueries({ queryKey: ["models"] });
}

export function useTraining() {
  return useQuery({ queryKey: ["training"], queryFn: api.getTraining, refetchInterval: 8000 });
}

export function useUpdateCollection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CollectionUpdate) => api.updateCollection(body),
    onSuccess: (data) => {
      qc.setQueryData(["training"], data);
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

export function useDatasets() {
  return useQuery({ queryKey: ["datasets"], queryFn: api.getDatasets, refetchInterval: 5000 });
}

export function useCreateDataset() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.createDataset, onSuccess: () => _invTraining(qc) });
}

export function useDeleteDataset() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: number) => api.deleteDataset(id), onSuccess: () => _invTraining(qc) });
}

export function useImportEvents() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: number) => api.importEvents(id), onSuccess: () => _invTraining(qc) });
}

export function useSamples(datasetId: number | null, label?: string) {
  return useQuery({
    queryKey: ["samples", datasetId, label ?? null],
    queryFn: () => api.getSamples(datasetId as number, label),
    enabled: datasetId != null,
    refetchInterval: 5000,
  });
}

export function useRelabelSample() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, label }: { id: number; label: string | null }) => api.relabelSample(id, label),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["samples"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

export function useDeleteSample() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.deleteSample(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["samples"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

export function useAnnotations(sampleId: number | null) {
  return useQuery({
    queryKey: ["annotations", sampleId],
    queryFn: () => api.getAnnotations(sampleId as number),
    enabled: sampleId !== null,
  });
}

export function useSetAnnotations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      sampleId,
      boxes,
    }: {
      sampleId: number;
      boxes: import("../types").AnnotationBox[];
    }) => api.setAnnotations(sampleId, boxes),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["annotations", vars.sampleId] });
      qc.invalidateQueries({ queryKey: ["samples"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

// Poll the active detection model for boxes on a live camera. Disabled (and
// silent) when no detection model is active — the server reports that cheaply.
export function useDetections(prefix: string, id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["detections", prefix, id],
    queryFn: () => api.detectObjects(prefix, id),
    enabled,
    refetchInterval: 1500,
    refetchIntervalInBackground: false,
  });
}

export function useModels() {
  return useQuery({ queryKey: ["models"], queryFn: api.getModels, refetchInterval: 8000 });
}

export function useRegisterModel() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.registerModel, onSuccess: () => _invTraining(qc) });
}

export function useActivateModel() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: number) => api.activateModel(id), onSuccess: () => _invTraining(qc) });
}

export function useDeactivateModel() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => api.deactivateModel(), onSuccess: () => _invTraining(qc) });
}

export function useDeleteModel() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: number) => api.deleteModel(id), onSuccess: () => _invTraining(qc) });
}

// -- active learning --

export function useActiveLearning() {
  // Poll fast while the loop runs so the status panel counts up live.
  return useQuery({
    queryKey: ["active-learning"],
    queryFn: api.getActiveLearning,
    refetchInterval: (q) => (q.state.data?.running ? 2500 : 10_000),
  });
}

export function useUpdateActiveLearning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: import("../types").ActiveLearningSettings) =>
      api.updateActiveLearning(body),
    onSuccess: (data) => qc.setQueryData(["active-learning"], data),
  });
}

export function useStartActiveLearning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startActiveLearning,
    onSuccess: (data) => {
      qc.setQueryData(["active-learning"], data);
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });
}

export function useStopActiveLearning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.stopActiveLearning,
    onSuccess: (data) => qc.setQueryData(["active-learning"], data),
  });
}

export function useLabelingBackends() {
  return useQuery({
    queryKey: ["al-backends"],
    queryFn: api.getLabelingBackends,
    refetchInterval: 30_000,
  });
}

export function useFinetuneBackends() {
  return useQuery({
    queryKey: ["al-finetune-backends"],
    queryFn: api.getFinetuneBackends,
    refetchInterval: 60_000,
  });
}

export function useTestLabelStudio() {
  return useMutation({ mutationFn: api.testLabelStudio });
}

export function useLabelStudioProjects(enabled: boolean) {
  return useQuery({
    queryKey: ["ls-projects"],
    queryFn: api.getLabelStudioProjects,
    enabled,
    retry: false,
  });
}

export function useSyncActiveLearning() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.syncActiveLearning,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["active-learning"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["samples"] });
    },
  });
}

export function useStartActiveLearningTrain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { epochs?: number }) => api.startActiveLearningTrain(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["active-learning"] });
    },
  });
}

export function useRuns() {
  // Poll fast only while a run is actually in flight; idle lists barely change.
  return useQuery({
    queryKey: ["runs"],
    queryFn: api.getRuns,
    refetchInterval: (q) =>
      (q.state.data ?? []).some((r) => ["queued", "preparing", "training"].includes(r.status))
        ? 2500
        : 12_000,
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startRun,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["training"] });
    },
  });
}

export function useStopRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.stopRun(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
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

export function useDetectionInfo() {
  return useQuery({
    queryKey: ["detection-info"],
    queryFn: api.getDetection,
    // Fast while the model is downloading so the progress bar moves.
    refetchInterval: (q) => (q.state.data?.status === "downloading" ? 1500 : 15_000),
  });
}

export function useUpdateDetection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.updateDetection,
    onSuccess: (data) => {
      qc.setQueryData(["detection-info"], data);
      qc.invalidateQueries({ queryKey: ["ai"] });
    },
  });
}

export function useAiTest() {
  return useMutation({
    mutationFn: (cameraId: string) => api.aiTest(cameraId),
  });
}

export function useUpdateAi() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: import("../types").AIUpdate) => api.updateAi(body),
    onSuccess: (data) => {
      qc.setQueryData(["ai"], data);
      qc.invalidateQueries({ queryKey: ["ai"] });
    },
  });
}

export function useOllamaModels() {
  return useQuery({ queryKey: ["ai-models"], queryFn: api.getOllamaModels, refetchInterval: 15_000 });
}

export function usePullModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (model: string) => api.pullModel(model),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-pull"] });
      qc.invalidateQueries({ queryKey: ["ai-models"] });
    },
  });
}

export function usePullProgress() {
  return useQuery({
    queryKey: ["ai-pull"],
    queryFn: api.getPullProgress,
    // Poll fast while a download is active; stop when idle.
    refetchInterval: (q) => (q.state.data?.active ? 1000 : false),
  });
}

export function useLoadModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (model: string) => api.loadModel(model),
    onSuccess: (data) => {
      qc.setQueryData(["ai"], data);
    },
  });
}

export function usePlugins() {
  return useQuery({ queryKey: ["plugins"], queryFn: api.getPlugins });
}

export function useMcp() {
  // MCP status is near-static config: it changes via this page's own mutation
  // (which updates the cache) or a rare external event (Tailscale coming up).
  // Don't poll on a timer — each GET forks a `tailscale status` subprocess;
  // refetch on focus/reconnect instead so re-opening the tab still refreshes.
  return useQuery({
    queryKey: ["mcp-info"],
    queryFn: api.getMcp,
    refetchOnWindowFocus: true,
  });
}

export function useUpdateMcp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.updateMcp,
    onSuccess: (data) => qc.setQueryData(["mcp-info"], data),
  });
}

export function usePluginsMarket() {
  return useQuery({ queryKey: ["plugins-market"], queryFn: () => api.getPluginsMarket() });
}

export function usePluginAction() {
  const qc = useQueryClient();
  const onSuccess = (data: import("../types").PluginsMarketInfo) => {
    qc.setQueryData(["plugins-market"], data);
    qc.invalidateQueries({ queryKey: ["plugins"] });
  };
  return {
    install: useMutation({ mutationFn: (id: string) => api.installPlugin(id), onSuccess }),
    uninstall: useMutation({ mutationFn: (stem: string) => api.uninstallPlugin(stem), onSuccess }),
    toggle: useMutation({
      mutationFn: (v: { stem: string; enabled: boolean }) => api.togglePlugin(v.stem, v.enabled),
      onSuccess,
    }),
    reload: useMutation({ mutationFn: () => api.reloadPlugins(), onSuccess }),
    refresh: useMutation({ mutationFn: () => api.getPluginsMarket(true), onSuccess }),
  };
}

export function useStorage() {
  return useQuery({ queryKey: ["storage"], queryFn: api.getStorage });
}

export function useUpdateStorage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: import("../types").StorageUpdate) => api.updateStorage(body),
    onSuccess: (data) => qc.setQueryData(["storage"], data),
  });
}

export function useIntegrations() {
  return useQuery({ queryKey: ["integrations"], queryFn: api.getIntegrations, refetchInterval: 15000 });
}

export function useUpdateHomeKit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: import("../types").HomeKitUpdate) => api.updateHomeKit(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations"] }),
  });
}

export function useResetHomeKit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.resetHomeKit(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations"] }),
  });
}

export function useUpdateHomeAssistant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: import("../types").HomeAssistantUpdate) => api.updateHomeAssistant(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations"] }),
  });
}

export function useNotifications() {
  return useQuery({ queryKey: ["notifications"], queryFn: api.getNotifications });
}

export function useUpdateNotifications() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: import("../types").NotificationsUpdate) => api.updateNotifications(body),
    onSuccess: (data) => qc.setQueryData(["notifications"], data),
  });
}

export function useTestNotification() {
  return useMutation({ mutationFn: api.testNotification });
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

export function useTimelapsePresets() {
  return useQuery({ queryKey: ["timelapse-presets"], queryFn: api.getTimelapsePresets });
}

export function useTimelapseAnalysisEvents(
  prefix: string,
  tlId: number | null,
  analysisEnabled = true,
) {
  return useQuery({
    queryKey: ["timelapse-analysis-events", prefix, tlId],
    queryFn: () => api.getTimelapseAnalysisEvents(prefix, tlId as number),
    // Only poll for printer captures that actually run analysis.
    enabled: tlId !== null && analysisEnabled,
    refetchInterval: 5000,
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

export function useSmoothTimelapse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ prefix, id, params }: { prefix: string; id: number; params?: TimelapseSmoothParams }) =>
      api.smoothTimelapse(prefix, id, params),
    onSuccess: () => _invalidateTimelapse(qc),
  });
}

export function usePostprocess() {
  return useQuery({ queryKey: ["postprocess"], queryFn: api.getPostprocess, refetchInterval: 60_000 });
}

export function useSetPostprocess() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { default_engine?: string }) => api.setPostprocess(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["postprocess"] }),
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
