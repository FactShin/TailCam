import type { HostInfo, NodeHealthInfo } from "../types";

export type FleetNodeSeverity = "healthy" | "warning" | "error" | "offline";

export interface FleetHealthInput {
  host: HostInfo;
  health?: NodeHealthInfo;
  error?: unknown;
}

export interface FleetHealthSummary {
  healthy: number;
  warning: number;
  error: number;
  offline: number;
  recording: number;
  cameraTotal: number;
  issueTotal: number;
}

export function nodeHealthSeverity(
  host: HostInfo,
  health?: NodeHealthInfo,
  currentVersion?: string | null,
  error?: unknown,
): FleetNodeSeverity {
  if (!host.online || error || !health) return "offline";
  if (health.issues.some((issue) => issue.severity === "error")) return "error";
  if (
    health.issues.some((issue) => issue.severity === "warning") ||
    health.update_available ||
    (currentVersion && health.version !== currentVersion)
  ) {
    return "warning";
  }
  return "healthy";
}

export function summarizeFleetHealth(
  nodes: FleetHealthInput[],
  currentVersion?: string | null,
): FleetHealthSummary {
  const summary: FleetHealthSummary = {
    healthy: 0,
    warning: 0,
    error: 0,
    offline: 0,
    recording: 0,
    cameraTotal: 0,
    issueTotal: 0,
  };

  for (const node of nodes) {
    summary[nodeHealthSeverity(node.host, node.health, currentVersion, node.error)] += 1;
    if (node.health) {
      summary.recording += node.health.camera_recording;
      summary.cameraTotal += node.health.camera_total;
      summary.issueTotal += node.health.issues.length;
    }
  }

  return summary;
}
