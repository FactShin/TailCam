import { jsonFetch } from "./client";
import type { DatasetRevision, ExperimentRequest, JobFilters, PlacementPolicy, PlacementPolicyStatus, SupervisionCreate, SupervisionRecord, SupervisionReport, WorkloadJob, WorkloadPage, WorkloadProvider, WorkloadWorker } from "../workloadTypes";

export const getPlacementPolicy = () => jsonFetch<PlacementPolicyStatus>("/api/v1/workloads/policy");
export const savePlacementPolicy = (body: { expected_revision: number; policy: PlacementPolicy }) =>
  jsonFetch<PlacementPolicyStatus>("/api/v1/workloads/policy", { method: "PATCH", body: JSON.stringify(body) });
export const getWorkloadWorkers = () => jsonFetch<{ items: WorkloadWorker[] }>("/api/v1/workloads/workers");
export const getWorkloadProviders = () => jsonFetch<{ items: WorkloadProvider[] }>("/api/v1/workloads/providers");
export const saveWorkloadProvider = (provider: WorkloadProvider) => jsonFetch<WorkloadProvider>("/api/v1/workloads/providers", { method: "POST", body: JSON.stringify(provider) });
export function getWorkloadJobs(filters: JobFilters, cursor?: string) {
  const params = new URLSearchParams();
  if (filters.task) params.set("task", filters.task);
  if (filters.state) params.set("state", filters.state);
  if (cursor) params.set("cursor", cursor);
  return jsonFetch<WorkloadPage<WorkloadJob>>(`/api/v1/jobs?${params}`);
}
export const getWorkloadJob = (id: string) => jsonFetch<WorkloadJob>(`/api/v1/jobs/${encodeURIComponent(id)}`);
export const controlWorkloadJob = ({ id, action }: { id: string; action: "cancel" | "retry" }) =>
  jsonFetch<WorkloadJob>(`/api/v1/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST", body: "{}" });

export const getSupervisions = (cursor?: string) => jsonFetch<WorkloadPage<SupervisionRecord>>(`/api/v1/training/supervisions${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`);
export const createSupervision = (body: SupervisionCreate) => jsonFetch<SupervisionRecord>("/api/v1/training/supervisions", { method: "POST", body: JSON.stringify(body) });
export const getSupervision = (id: string) => jsonFetch<SupervisionRecord>(`/api/v1/training/supervisions/${encodeURIComponent(id)}`);
export const submitExperiment = ({ id, request }: { id: string; request: ExperimentRequest }) =>
  jsonFetch<SupervisionRecord>(`/api/v1/training/supervisions/${encodeURIComponent(id)}/experiments`, { method: "POST", body: JSON.stringify(request) });
export const controlSupervision = ({ id, action, reason }: { id: string; action: "stop" | "finish"; reason?: string }) =>
  jsonFetch<SupervisionRecord>(`/api/v1/training/supervisions/${encodeURIComponent(id)}/${action}`, { method: "POST", body: JSON.stringify(action === "finish" ? { reason } : {}) });
export const getDatasetRevision = (id: number) => jsonFetch<DatasetRevision>(`/api/v1/training/datasets/${id}/revision`);
export const getSupervisionReport = (id: string) => jsonFetch<SupervisionReport>(`/api/v1/training/supervisions/${encodeURIComponent(id)}/report`);
