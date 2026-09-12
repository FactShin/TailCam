import { createServer, type Server } from "node:http";
import { expect, test } from "@playwright/test";
import type { CameraInfo, MediaInfo, NodeConfig, NodeReadiness, StorageInfo, SystemInfo } from "../src/types";

const camera: CameraInfo = {
  id: "/dev/video0",
  name: "Browser fixture camera",
  backend: "synthetic",
  status: "offline",
  fps: 0,
  width: 640,
  height: 480,
  recording: false,
  motion_enabled: false,
  detection_enabled: false,
  detection_override: null,
  properties: {},
  transform: { rotation: 0, flip_h: false, flip_v: false },
  stream: { fps: 10, quality: 70, max_width: 640 },
  stream_overrides: { fps: null, quality: null, max_width: null },
  last_error: "Offline fixture; no camera hardware is used",
  host: "pi-capture",
  proxy_prefix: "/proxy/capture",
};
const media: MediaInfo = {
  id: 42,
  camera_id: camera.id,
  media_type: "recording",
  created_ts: 1_700_000_000,
  trigger: "motion",
  size_bytes: 100,
  has_thumbnail: false,
  host: "storage",
  proxy_prefix: "/proxy/storage",
  source_host: camera.host,
};

// Real HTTP responses, rather than Playwright routing: interception would
// bypass the browser's service worker and make the cache checks misleading.
let backend: Server;
const requested = new Set<string>();
const probes = new Map<string, number>();
const allRoles = ["capture", "storage", "analysis", "training"];
let nodeConfig: NodeConfig;
let configReadStatus = 200;
let configWriteStatus = 200;
let visibleCameras: CameraInfo[] = [];
let lastNodePatch: unknown;
let fleetRefreshes = 0;
let storageFixture: StorageInfo | undefined;
let readinessSnapshot: NodeReadiness;
let peerReadiness: NodeReadiness | null | undefined;
let readinessReadStatus = 200;
let readinessProbeStatus = 200;
let readinessProbeCount = 0;
let deferRuntimeProbe = false;
let finishRuntimeProbe: (() => void) | undefined;
const requestedUrls: string[] = [];
test.beforeAll(async () => {
  backend = createServer((req, res) => {
    const requestUrl = new URL(req.url!, "http://127.0.0.1:4174");
    const path = requestUrl.pathname;
    requestedUrls.push(req.url!);
    requested.add(path);
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Type", "application/json");
    if (path === "/api/v1/node/config") {
      if (req.method === "PATCH") {
        let body = "";
        req.on("data", (chunk) => { body += chunk; });
        req.on("end", () => {
          lastNodePatch = JSON.parse(body);
          if (configWriteStatus !== 200) {
            res.writeHead(configWriteStatus).end(JSON.stringify({ detail: "admin role required" }));
            return;
          }
          const update = lastNodePatch as { name?: string; roles?: string[] };
          const configuredRoles = update.roles ?? nodeConfig.configured_roles;
          nodeConfig = {
            ...nodeConfig, name: update.name ?? nodeConfig.name, configured_roles: configuredRoles,
            restart_required: JSON.stringify([...configuredRoles].sort()) !== JSON.stringify([...nodeConfig.active_roles].sort()),
          };
          res.end(JSON.stringify(nodeConfig));
        });
        return;
      }
      if (configReadStatus !== 200) {
        res.writeHead(configReadStatus).end(JSON.stringify({ detail: "Node configuration unavailable" }));
        return;
      }
      res.end(JSON.stringify(nodeConfig));
      return;
    }
    if (path === "/api/cameras/refresh" && req.method === "POST") {
      fleetRefreshes += 1;
      res.end(JSON.stringify(visibleCameras));
      return;
    }
    if (req.method !== "GET") {
      res.writeHead(405).end(JSON.stringify({ detail: "Read-only browser fixture" }));
      return;
    }
    if (path === "/api/v1/node/capabilities" || path === "/api/v1/fleet/nodes/capture/capabilities") {
      const probe = requestUrl.searchParams.get("probe") === "true";
      const send = () => {
        const status = probe ? readinessProbeStatus : readinessReadStatus;
        if (status !== 200) {
          res.writeHead(status).end(JSON.stringify({ detail: "Runtime status is temporarily unavailable" }));
          return;
        }
        const snapshot = path === "/api/v1/node/capabilities" ? readinessSnapshot : peerReadiness;
        res.end(JSON.stringify({
          api_version: "1", capabilities: ["camera.view", "node.health"], actions: ["reload"],
          principal: { actor: "local", display_name: "Local", source: "local", verified: true, roles: ["admin"] },
          ...(snapshot === undefined ? {} : { readiness: snapshot }),
        }));
      };
      if (probe) readinessProbeCount += 1;
      if (probe && deferRuntimeProbe) {
        finishRuntimeProbe = send;
      } else {
        send();
      }
      return;
    }
    if (path.endsWith("/browser-probe")) {
      const count = (probes.get(path) ?? 0) + 1;
      probes.set(path, count);
      res.end(JSON.stringify({ path, count }));
      return;
    }
    const fixtures: Record<string, unknown> = {
      ...(storageFixture ? { "/api/storage": storageFixture } : {}),
      "/api/cameras": visibleCameras,
      "/api/hosts": [{
        host: "browser-hub", node_key: "local", kind: "local", online: true,
        version: "1.9.0", camera_count: 0, proxy_prefix: "",
        node_id: nodeConfig.node_id, node_name: nodeConfig.name, node_roles: nodeConfig.active_roles,
      }, {
        host: camera.host, node_key: "capture", kind: "peer", online: true,
        version: "1.8.6", camera_count: 1, proxy_prefix: camera.proxy_prefix,
      }],
      "/api/system": {
        version: "1.9.0", host: "browser-hub", node_id: nodeConfig.node_id,
        node_name: nodeConfig.name, node_roles: nodeConfig.active_roles,
        tailscale_installed: false, tailscale_running: false,
        access_url: "http://localhost:4173", local_url: "http://localhost:4173",
        media_bytes: 0, hidden_count: 0, host_model: "Browser fixture",
        ram_gb: 8, cpu_count: 4, low_power: false,
      } satisfies SystemInfo,
      "/api/update": { current: "1.8.6", latest: "1.8.6", available: false },
      "/api/media": [],
      "/api/events": [],
      "/api/models": [],
      "/api/datasets": [],
      "/api/training/runs": [],
      "/proxy/storage/api/media/42": media,
    };
    if (path in fixtures) {
      res.end(JSON.stringify(fixtures[path]));
      return;
    }
    res.writeHead(404).end(JSON.stringify({ detail: "Unavailable in browser fixture" }));
  });
  await new Promise<void>((resolve, reject) => {
    backend.once("error", reject);
    backend.listen(4174, "127.0.0.1", resolve);
  });
});

test.afterAll(async () => {
  if (!backend?.listening) return;
  await new Promise<void>((resolve, reject) => {
    backend.close((error) => error ? reject(error) : resolve());
    backend.closeAllConnections();
  });
});

test.beforeEach(async ({ page }) => {
  requested.clear();
  probes.clear();
  nodeConfig = {
    node_id: "49ba90ab-a97a-4c6d-9ac8-1cfc2c48c7a9", name: "Studio",
    configured_roles: [...allRoles], active_roles: [...allRoles], restart_required: false,
  };
  configReadStatus = 200;
  configWriteStatus = 200;
  visibleCameras = [camera];
  lastNodePatch = undefined;
  fleetRefreshes = 0;
  storageFixture = undefined;
  const checkedAt = Math.floor(Date.now() / 1000);
  readinessSnapshot = {
    checked_at: checkedAt,
    capacity: { cpu_count: 4, total_ram_bytes: 8 * 1024 ** 3, media_free_bytes: 0, media_total_bytes: 16 * 1024 ** 3, media_writable: false },
    tasks: [
      { id: "camera.capture", label: "Attached cameras", state: "ready", code: "camera.ready", detail: "One local camera is online.", checked_at: checkedAt },
      { id: "media.write", label: "Local media writes", state: "unavailable", code: "storage.not_writable", detail: "The media directory is not writable.", checked_at: checkedAt },
      { id: "vision.detect", label: "Vision detection", state: "disabled", code: "role.disabled", detail: "The analysis role is disabled.", checked_at: checkedAt },
      { id: "printer.analyze", label: "Printer analysis", state: "unchecked", code: "runtime.unchecked", detail: "The configured runtime has not been checked.", checked_at: 0 },
    ],
    probe_supported: true,
  };
  peerReadiness = undefined;
  readinessReadStatus = 200;
  readinessProbeStatus = 200;
  readinessProbeCount = 0;
  requestedUrls.length = 0;
  deferRuntimeProbe = false;
  finishRuntimeProbe = undefined;
  await page.addInitScript(() => sessionStorage.setItem("tailcam.booted", "1"));
});

test.afterEach(() => {
  finishRuntimeProbe?.();
  finishRuntimeProbe = undefined;
});

test("mobile readiness explains mixed task states and capacity without probing", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Runtime readiness", exact: true });
  await expect(panel.getByRole("list", { name: "Task readiness" })).toBeVisible();
  await expect(panel.getByRole("listitem").filter({ hasText: "Attached cameras" })).toContainText("Ready");
  await expect(panel.getByRole("listitem").filter({ hasText: "Local media writes" })).toContainText("Unavailable");
  await expect(panel.getByRole("listitem").filter({ hasText: "Vision detection" })).toContainText("Disabled");
  await expect(panel.getByRole("listitem").filter({ hasText: "Printer analysis" })).toContainText("Not checked");
  await expect(panel.getByText("The media directory is not writable.", { exact: true })).toBeVisible();
  await expect(panel.locator(".readiness-capacity")).toContainText("4 logical CPUs");
  await expect(panel.locator(".readiness-capacity")).toContainText("8.0 GB");
  await expect(panel.locator(".readiness-capacity")).toContainText("0 B free");
  await expect(panel.locator(".readiness-capacity")).toContainText("Not writable");
  expect(readinessProbeCount).toBe(0);
  expect(requestedUrls.filter((url) => url.includes("probe=true"))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("readiness-mobile.png") });
});

test("Settings polls passive diagnostics without loading AI and probes only when requested", async ({ page }) => {
  await page.clock.install();
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Runtime readiness", exact: true });
  await expect(panel.getByRole("button", { name: "Check runtimes", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open AI Studio", exact: true })).toHaveAttribute("href", "/ai");

  // Exercise the periodic refresh too: opening Settings must not merely delay
  // a legacy /api/ai call until its polling interval.
  await page.clock.fastForward(31_000);
  await expect.poll(() => requestedUrls.filter((url) => url === "/api/v1/node/capabilities").length).toBeGreaterThan(1);
  expect(requestedUrls.filter((url) => /^\/api\/ai(?:[/?]|$)/.test(url))).toEqual([]);
  expect(requestedUrls.filter((url) => url.includes("probe=true"))).toEqual([]);

  await panel.getByRole("button", { name: "Check runtimes", exact: true }).click();
  await expect.poll(() => readinessProbeCount).toBe(1);
  await expect(panel.getByRole("button", { name: "Check runtimes", exact: true })).toBeEnabled();
  expect(requestedUrls.filter((url) => url.includes("probe=true"))).toEqual([
    "/api/v1/node/capabilities?probe=true",
  ]);
  expect(requestedUrls.filter((url) => /^\/api\/ai(?:[/?]|$)/.test(url))).toEqual([]);
});

test("explicit runtime check retains stale results after failure and retries", async ({ page }) => {
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Runtime readiness", exact: true });
  await expect(panel.getByRole("button", { name: "Check runtimes", exact: true })).toBeVisible();
  deferRuntimeProbe = true;
  await panel.getByRole("button", { name: "Check runtimes", exact: true }).click();
  await expect.poll(() => readinessProbeCount).toBe(1);
  await expect(panel.getByRole("button", { name: "Checking runtimes…", exact: true })).toBeDisabled();
  await expect(panel.getByRole("status")).toContainText("Models are not loaded or downloaded");
  readinessProbeStatus = 503;
  finishRuntimeProbe?.();
  finishRuntimeProbe = undefined;
  await expect(panel.getByRole("alert")).toContainText("Stale snapshot");
  await expect(panel.getByRole("listitem").filter({ hasText: "Attached cameras" })).toContainText("One local camera is online.");
  await expect(panel.getByRole("listitem").filter({ hasText: "Printer analysis" })).toContainText("Not checked");

  readinessProbeStatus = 200;
  deferRuntimeProbe = false;
  readinessSnapshot = {
    ...readinessSnapshot,
    tasks: readinessSnapshot.tasks.map((task) => task.id === "printer.analyze"
      ? { ...task, state: "ready", code: "model.available", detail: "The configured model is available.", checked_at: Math.floor(Date.now() / 1000) }
      : task),
  };
  await panel.getByRole("button", { name: "Retry runtime check", exact: true }).click();
  await expect(panel.getByRole("listitem").filter({ hasText: "Printer analysis" })).toContainText("The configured model is available.");
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect(readinessProbeCount).toBe(2);
  expect(requestedUrls.filter((url) => url.includes("probe=true"))).toEqual([
    "/api/v1/node/capabilities?probe=true", "/api/v1/node/capabilities?probe=true",
  ]);
});

for (const legacyState of ["missing", "null"] as const) {
  test(`legacy peer with ${legacyState} readiness never inherits local healthy results`, async ({ page }) => {
    peerReadiness = legacyState === "missing" ? undefined : null;
    await page.goto("/settings");
    const panel = page.getByRole("region", { name: "Runtime readiness", exact: true });
    await expect(panel.getByRole("list", { name: "Task readiness" })).toBeVisible();
    await panel.getByLabel("Check device").selectOption("capture");
    await expect(panel.getByText("Readiness is not reported by this node.", { exact: false })).toBeVisible();
    await expect(panel.getByRole("list", { name: "Task readiness" })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "Check runtimes", exact: true })).toHaveCount(0);
    expect(requestedUrls).toContain("/api/v1/fleet/nodes/capture/capabilities");
    expect(readinessProbeCount).toBe(0);
  });
}

test("initial readiness error retries a cheap snapshot without a runtime probe", async ({ page }) => {
  readinessReadStatus = 503;
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Runtime readiness", exact: true });
  await expect(panel.getByRole("alert")).toContainText("Could not load this node’s readiness");
  readinessReadStatus = 200;
  await panel.getByRole("button", { name: "Retry readiness", exact: true }).click();
  await expect(panel.getByRole("list", { name: "Task readiness" })).toBeVisible();
  expect(readinessProbeCount).toBe(0);
});

test("peer runtime checks remain attached to their node when selection changes", async ({ page }) => {
  peerReadiness = {
    ...readinessSnapshot,
    tasks: [{ id: "timelapse.encode", label: "Peer encoding", state: "ready", code: "runtime.ready", detail: "The peer encoder is available.", checked_at: readinessSnapshot.checked_at }],
  };
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Runtime readiness", exact: true });
  await panel.getByLabel("Check device").selectOption("capture");
  await expect(panel.getByRole("listitem")).toContainText("Peer encoding");
  deferRuntimeProbe = true;
  await panel.getByRole("button", { name: "Check runtimes", exact: true }).click();
  await expect.poll(() => readinessProbeCount).toBe(1);
  expect(requestedUrls).toContain("/api/v1/fleet/nodes/capture/capabilities?probe=true");
  await panel.getByLabel("Check device").selectOption("local");
  finishRuntimeProbe?.();
  finishRuntimeProbe = undefined;
  await expect(panel.getByRole("listitem").filter({ hasText: "Attached cameras" })).toBeVisible();
  await expect(panel.getByText("Peer encoding", { exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Check runtimes", exact: true })).toBeEnabled();
});

test("mobile node purpose saves future roles without claiming active capture stopped", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Node purpose", exact: true });
  await expect(panel.getByLabel("Device name")).toHaveValue("Studio");
  await panel.getByRole("checkbox", { name: "Training", exact: true }).uncheck();
  await expect(panel.getByLabel("Purpose preset")).toHaveValue("custom");
  await panel.getByRole("button", { name: "Discard changes", exact: true }).click();
  await expect(panel.getByRole("checkbox", { name: "Training", exact: true })).toBeChecked();
  await panel.getByLabel("Device name").fill("Living room hub");
  await panel.getByLabel("Purpose preset").selectOption("hub");
  await expect(panel.getByRole("checkbox", { name: "Camera capture", exact: true })).not.toBeChecked();
  await panel.getByRole("button", { name: "Save purpose", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("Restart required");
  await expect(panel.locator(".node-purpose-current")).toContainText("Camera capture");
  await expect(panel.getByRole("status")).toContainText("Saved for the next start: Hub · view and control");
  expect(lastNodePatch).toEqual({ name: "Living room hub", roles: [] });
  await panel.getByText("Persistent node ID", { exact: true }).click();
  await expect(panel.getByText(nodeConfig.node_id, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.reload();
  await expect(panel.getByLabel("Device name")).toHaveValue("Living room hub");
  await expect(panel.getByLabel("Purpose preset")).toHaveValue("hub");
  await expect(panel.getByRole("status")).toContainText("Restart required");
  await expect(page.getByText("Roles not reported", { exact: true })).toBeVisible();
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("node-purpose-mobile.png") });
  await panel.getByRole("status").scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("node-purpose-mobile-restart.png") });
});

test("node purpose preserves rejected edits and recovers from a read failure", async ({ page }, testInfo) => {
  configReadStatus = 503;
  await page.goto("/settings");
  const panel = page.getByRole("region", { name: "Node purpose", exact: true });
  await expect(panel.getByRole("alert")).toContainText("Could not load");
  configReadStatus = 200;
  await panel.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(panel.getByLabel("Device name")).toHaveValue("Studio");
  configWriteStatus = 403;
  await panel.getByLabel("Purpose preset").selectOption("storage");
  await panel.getByRole("button", { name: "Save purpose", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("Admin access is required");
  await expect(panel.getByLabel("Purpose preset")).toHaveValue("storage");
  expect(nodeConfig.configured_roles).toEqual(allRoles);
  configWriteStatus = 200;
  await panel.getByRole("button", { name: "Save purpose", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("Restart required");
  expect(nodeConfig.configured_roles).toEqual(["storage"]);
  expect(lastNodePatch).toEqual({ roles: ["storage"] });
  expect(nodeConfig.name).toBe("Studio");
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("node-purpose-desktop.png") });
});

test("hub empty state refreshes the fleet and keeps peer camera navigation", async ({ page }) => {
  nodeConfig.active_roles = [];
  nodeConfig.configured_roles = [];
  visibleCameras = [];
  await page.goto("/");
  await expect(page.getByText("This device does not capture cameras", { exact: true })).toBeVisible();
  await expect(page.getByText("Plug in a USB camera", { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: "Refresh fleet", exact: true }).first().click();
  await expect.poll(() => fleetRefreshes).toBe(1);
  visibleCameras = [camera];
  await page.getByRole("button", { name: "Refresh fleet", exact: true }).first().click();
  await expect(page.getByText(camera.name, { exact: true })).toBeVisible();
  await page.getByText(camera.name, { exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/camera/${camera.host}/`));
  await expect(page.getByRole("heading", { name: camera.name, exact: true })).toBeVisible();
});

test("storage picker explains disabled roles while keeping older peers compatible", async ({ page }) => {
  storageFixture = {
    media_dir: "/fixture/media", custom_dir: "", is_default: true,
    writable: true, storage_enabled: false, disk_total: 1000, disk_free: 900,
    disk_used: 100, media_bytes: 0, media_count: 0, timelapse_bytes: 0,
    node: "", node_online: true, node_error: "", low_power_host: false,
    auto_record: true, record_tail_seconds: 5, retention_enabled: false,
    max_gb: 10, max_age_days: 30,
    nodes: [
      { node_key: "local", host: "browser-hub", online: true, storage_enabled: false, media_dir: "/fixture/media", disk_total: 1000, disk_free: 900, version: "1.9.0" },
      { node_key: "compute", host: "compute-worker", online: true, storage_enabled: false, media_dir: "/fixture/media", disk_total: 1000, disk_free: 900, version: "1.9.0" },
      { node_key: "legacy", host: "older-storage", online: true, media_dir: "/fixture/media", disk_total: 1000, disk_free: 900, version: "1.8.6" },
    ],
  };
  await page.goto("/settings");
  await expect(page.getByRole("button", { name: /browser-hub \(this device\) Storage role off/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: /compute-worker Storage role off/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: /older-storage.*free/ })).toBeEnabled();
  await expect(page.getByText("The Storage role is disabled here.", { exact: false })).toBeVisible();
  await expect(page.getByText("not writable", { exact: true })).toHaveCount(0);
});

test("direct camera and docs routes preserve encoded IDs and browser history", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`/camera/${camera.host}/${encodeURIComponent(camera.id)}`);
  await expect(page.getByRole("heading", { name: camera.name, exact: true })).toBeVisible();
  await expect(page.locator(".detail-meta")).toContainText(`${camera.host} · ${camera.id}`);

  await page.goto("/docs/installation");
  await expect(page.locator(".docs-head").getByRole("heading", { name: "Installation", exact: true })).toBeVisible();
  await page.locator(".side-nav").getByRole("button", { name: "Gallery", exact: false }).click();
  await expect(page).toHaveURL(/\/gallery$/);
  await expect(page.getByRole("heading", { name: "Gallery", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/docs\/installation$/);
  await expect(page.locator(".docs-head").getByRole("heading", { name: "Installation", exact: true })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole("heading", { name: "Gallery", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("gallery opens the owner-qualified clip and preserves unrelated query parameters on close", async ({ page }) => {
  await page.goto("/gallery?media=42&owner=%2Fproxy%2Fstorage&keep=yes");
  const viewer = page.getByRole("dialog", { name: "Media viewer" });
  await expect(viewer).toBeVisible();
  await expect(viewer.getByRole("link", { name: "Download" })).toHaveAttribute("href", "/proxy/storage/media/42/file");
  expect(requested.has("/proxy/storage/api/media/42")).toBe(true);
  expect(requested.has("/api/media/42")).toBe(false);
  await viewer.getByRole("button", { name: "Close", exact: true }).click();
  await expect(viewer).not.toBeVisible();
  await expect(page).toHaveURL(/\/gallery\?keep=yes$/);
});

for (const tab of ["training", "models"] as const) {
  test(`legacy /${tab} links replace history and activate the matching AI Studio tab`, async ({ page }) => {
    await page.goto("/docs/installation");
    await page.goto(`/${tab}`);
    await expect(page).toHaveURL(new RegExp(`/ai\\?tab=${tab}$`));
    await expect(page.getByRole("heading", { name: "AI Studio", exact: true })).toBeVisible();
    await expect(page.getByRole("radio", { name: tab, exact: false })).toBeChecked();
    await page.goBack();
    await expect(page).toHaveURL(/\/docs\/installation$/);
  });
}

test("PWA reloads its shell offline while API and media stay network-only", async ({ page, context }) => {
  await page.goto("/docs/installation");
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
  const reload = await page.reload();
  expect(reload?.fromServiceWorker()).toBe(true);
  await expect(page.locator(".docs-head").getByRole("heading", { name: "Installation", exact: true })).toBeVisible();

  const livePaths = [
    "/api/browser-probe", "/media/browser-probe",
    "/proxy/storage/media/browser-probe", "/stream/browser-probe",
  ];
  for (const path of livePaths) {
    const counts = await page.evaluate(async (url) => {
      const first = await fetch(url, { cache: "no-store" }).then((r) => r.json());
      const second = await fetch(url, { cache: "no-store" }).then((r) => r.json());
      return [first.count, second.count];
    }, path);
    expect(counts).toEqual([1, 2]);
  }

  await context.setOffline(true);
  const offlineReload = await page.reload();
  expect(offlineReload?.fromServiceWorker()).toBe(true);
  await expect(page.locator(".docs-head").getByRole("heading", { name: "Installation", exact: true })).toBeVisible();
  for (const path of livePaths) {
    const result = await page.evaluate(async (url) => {
      try {
        const response = await fetch(url, { cache: "no-store" });
        return { failed: false, body: await response.text() };
      } catch {
        return { failed: true };
      }
    }, path);
    expect(result).toEqual({ failed: true });
    // Navigating directly to a live endpoint must fail too, rather than
    // receiving the cached index.html as an accidental SPA fallback.
    const livePage = await context.newPage();
    try {
      await expect(livePage.goto(path)).rejects.toThrow();
    } finally {
      await livePage.close();
    }
  }
  const cachedPaths = await page.evaluate(async () => {
    const caches = await window.caches.keys();
    const requests = await Promise.all(caches.map(async (key) => (await window.caches.open(key)).keys()));
    return requests.flat().map((request) => new URL(request.url).pathname);
  });
  for (const path of livePaths) expect(cachedPaths).not.toContain(path);

  const home = await page.goto("/");
  expect(home?.fromServiceWorker()).toBe(true);
  await expect(page.locator(".sidebar").getByRole("button", { name: "TailCam home" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Connection lost" })).toBeVisible();
});
