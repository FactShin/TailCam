import type { ReactNode } from "react";
import type { HostInfo } from "../types";
import type { JobState, WorkerTarget } from "../workloadTypes";

export function WorkloadNotice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`workload-notice ${error ? "is-error" : ""}`} role={error ? "alert" : "status"}>{children}</div>;
}
export function workloadError(error: unknown): string {
  return error instanceof Error ? error.message : "The request could not be completed. Try again.";
}
export function workloadTime(value: number | null | undefined): string {
  return value != null && Number.isFinite(value) ? new Date(value * 1000).toLocaleString() : "Not reported";
}
export function workloadDuration(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "Not reported";
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}
export function workloadNode(id: string | null | undefined, hosts: HostInfo[]): string {
  if (!id) return "Not assigned";
  const host = hosts.find(item => item.node_id === id);
  return host?.node_name || host?.host || `Node ${id}`;
}
export function targetLabel(target: WorkerTarget | null | undefined, hosts: HostInfo[]): string {
  if (!target) return "Not assigned";
  return [target.node_id ? workloadNode(target.node_id, hosts) : null, target.provider_id ? `Endpoint ${target.provider_id}` : null, target.model || null].filter(Boolean).join(" · ") || "Not assigned";
}
const STATES: Record<JobState, string> = {
  queued: "Queued", leased: "Worker assigned", running: "Running", committing: "Committing outputs",
  retry_wait: "Waiting to retry", succeeded: "Completed", failed: "Failed", cancel_requested: "Cancel requested",
  cancelled: "Stopped", deadline_exceeded: "Deadline exceeded", waiting_for_worker: "Waiting for worker",
};
export function JobStatus({ state }: { state: JobState }) {
  const tone = state === "succeeded" ? "badge-ok" : ["failed", "deadline_exceeded"].includes(state) ? "badge-err" : "badge-warn";
  return <span className={`badge ${tone}`}>{STATES[state] ?? state}</span>;
}
