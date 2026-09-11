import { createServer, type Server } from "node:http";
import { expect, test } from "@playwright/test";
import type { CameraInfo, MediaInfo } from "../src/types";

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
test.beforeAll(async () => {
  backend = createServer((req, res) => {
    const path = new URL(req.url!, "http://127.0.0.1:4174").pathname;
    requested.add(path);
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Type", "application/json");
    if (req.method !== "GET") {
      res.writeHead(405).end(JSON.stringify({ detail: "Read-only browser fixture" }));
      return;
    }
    if (path.endsWith("/browser-probe")) {
      const count = (probes.get(path) ?? 0) + 1;
      probes.set(path, count);
      res.end(JSON.stringify({ path, count }));
      return;
    }
    const fixtures: Record<string, unknown> = {
      "/api/cameras": [camera],
      "/api/hosts": [{
        host: camera.host, node_key: "capture", kind: "peer", online: true,
        version: "1.8.6", camera_count: 1, proxy_prefix: camera.proxy_prefix,
      }],
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
  await page.addInitScript(() => sessionStorage.setItem("tailcam.booted", "1"));
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
