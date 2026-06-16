import { describe, expect, test } from "vitest";

import { nodeHealthSeverity, summarizeFleetHealth } from "./fleet";
import type { HostInfo, NodeHealthInfo } from "../types";

const host = (key: string, online = true): HostInfo => ({
  host: key,
  node_key: key,
  kind: key === "local" ? "local" : "peer",
  online,
  version: "0.90.0",
  camera_count: 2,
  proxy_prefix: key === "local" ? "" : `/proxy/${key}`,
});

const health = (partial: Partial<NodeHealthInfo> = {}): NodeHealthInfo => ({
  host: "local",
  version: "0.90.0",
  platform: "Darwin arm64",
  python_version: "3.12.0",
  uptime_seconds: 10,
  tailscale_installed: true,
  tailscale_running: true,
  tailscale_served: true,
  access_url: "https://tailcam.example.ts.net:8443/",
  local_url: "http://localhost:8088/",
  camera_total: 2,
  camera_online: 2,
  camera_offline: 0,
  camera_degraded: 0,
  camera_recording: 0,
  media_bytes: 1024,
  timelapse_bytes: 0,
  update_current: "0.90.0",
  update_latest: "0.90.0",
  update_available: false,
  ai_enabled: true,
  ai_reachable: true,
  ai_model: "llava:7b",
  ai_model_present: true,
  issues: [],
  ...partial,
});

describe("fleet health severity", () => {
  test("aggregates healthy warning error and offline nodes independently", () => {
    const summary = summarizeFleetHealth([
      { host: host("local"), health: health() },
      {
        host: host("garage"),
        health: health({ issues: [{ code: "camera.offline", severity: "warning", summary: "1 camera offline", detail: null }] }),
      },
      {
        host: host("studio"),
        health: health({ issues: [{ code: "node.failure", severity: "error", summary: "Node failed", detail: null }] }),
      },
      { host: host("shed", false), error: new Error("unreachable") },
    ]);

    expect(summary).toEqual({
      healthy: 1,
      warning: 1,
      error: 1,
      offline: 1,
      recording: 0,
      cameraTotal: 6,
      issueTotal: 2,
    });
  });

  test("reports warning when a node has version drift or an update available", () => {
    expect(nodeHealthSeverity(host("garage", true), health({ version: "0.89.0" }), "0.90.0")).toBe("warning");
    expect(nodeHealthSeverity(host("garage", true), health({ update_available: true }), "0.90.0")).toBe("warning");
  });
});
