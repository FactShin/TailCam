// mock.jsx — mock AnyCam API, in-memory store, polling hooks, and synthetic feed renderer.
// Mirrors the documented endpoints. No network — everything is local + self-contained.

const { useState, useEffect, useRef, useCallback } = React;

// ---------------------------------------------------------------------------
// streamUrl helper — builds same-origin URLs. Camera ids may contain slashes;
// we DO NOT url-encode the slashes (the backend uses a path matcher).
// ---------------------------------------------------------------------------
function streamUrl(id, params) {
  const qs = params
    ? "?" + Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k, v]) => `${k}=${v}`)
        .join("&")
    : "";
  return `/stream/${id}.mjpg${qs}`;
}
const snapshotUrl = (id) => `/stream/${id}/snapshot.jpg`;
const mediaFileUrl = (mid) => `/media/${mid}/file`;
const mediaThumbUrl = (mid) => `/media/${mid}/thumbnail`;
const cacheBust = () => `_=${Date.now()}`;

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------
const NOW = () => Date.now() / 1000;

const SCENES = {
  "/dev/video0": { kind: "porch",  sky: ["#1b2540", "#43506e"], ground: "#222a1c", subject: "person", speed: 0.10 },
  "/dev/video2": { kind: "yard",   sky: ["#10233a", "#2b3d4f"], ground: "#1c2a1a", subject: "pet",    speed: 0.16 },
  "0":           { kind: "desk",   sky: ["#181b22", "#23262f"], ground: "#15171d", subject: "none",   speed: 0 },
  "synthetic-0": { kind: "pattern",sky: ["#0d0f14", "#0d0f14"], ground: "#0d0f14", subject: "none",   speed: 0 },
  "/dev/video4": { kind: "garage", sky: ["#14161c", "#1d2027"], ground: "#101216", subject: "none",   speed: 0 },
  "synthetic-1": { kind: "crib",   sky: ["#241a2e", "#3a2c44"], ground: "#1a1422", subject: "pet",    speed: 0.07 },
};

let _cameras = [
  { id: "/dev/video0", name: "Front Door", backend: "v4l2", status: "online", fps: 14.8, width: 1280, height: 720, recording: true, motion_enabled: true,
    properties: { width: 1280, height: 720, fps: 15, brightness: 56, contrast: 50, saturation: 60 }, transform: { rotation: 0, flip_h: false, flip_v: false } },
  { id: "/dev/video2", name: "Backyard", backend: "v4l2", status: "degraded", fps: 6.2, width: 1280, height: 720, recording: false, motion_enabled: true,
    properties: { width: 1280, height: 720, fps: 15, brightness: 48, contrast: 55, saturation: 45 }, transform: { rotation: 0, flip_h: false, flip_v: false } },
  { id: "0", name: "Office (Mac)", backend: "avfoundation", status: "online", fps: 29.6, width: 1920, height: 1080, recording: false, motion_enabled: false,
    properties: { width: 1920, height: 1080, fps: 30, brightness: 50, contrast: 50, saturation: 50 }, transform: { rotation: 0, flip_h: false, flip_v: false } },
  { id: "synthetic-0", name: "Test Pattern", backend: "synthetic", status: "online", fps: 30.0, width: 640, height: 480, recording: false, motion_enabled: false,
    properties: { width: 640, height: 480, fps: 30, brightness: 50, contrast: 50, saturation: 50 }, transform: { rotation: 0, flip_h: false, flip_v: false } },
  { id: "/dev/video4", name: "Garage", backend: "v4l2", status: "offline", fps: 0, width: 1280, height: 720, recording: false, motion_enabled: true,
    properties: { width: 1280, height: 720, fps: 15, brightness: 50, contrast: 50, saturation: 50 }, transform: { rotation: 90, flip_h: false, flip_v: false } },
  { id: "synthetic-1", name: "Nursery", backend: "synthetic", status: "online", fps: 15.0, width: 1280, height: 720, recording: false, motion_enabled: true,
    properties: { width: 1280, height: 720, fps: 15, brightness: 62, contrast: 45, saturation: 55 }, transform: { rotation: 0, flip_h: false, flip_v: false } },
];

const camName = (id) => (_cameras.find((c) => c.id === id) || {}).name || id;

let _mediaSeq = 240;
let _media = [];
let _events = [];

(function seedMediaAndEvents() {
  const t0 = NOW();
  const defs = [
    { id: "/dev/video0", n: 9,  rec: 3 },
    { id: "/dev/video2", n: 6,  rec: 2 },
    { id: "0",           n: 4,  rec: 0 },
    { id: "synthetic-1", n: 7,  rec: 3 },
    { id: "/dev/video4", n: 3,  rec: 1 },
  ];
  defs.forEach((d) => {
    for (let i = 0; i < d.n; i++) {
      const isRec = i < d.rec;
      const mid = _mediaSeq--;
      const ts = t0 - (i * 2400 + Math.random() * 1500) - defs.indexOf(d) * 600;
      _media.push({
        id: mid, camera_id: d.id, media_type: isRec ? "recording" : "snapshot",
        created_ts: ts, trigger: Math.random() > 0.45 ? "motion" : "manual",
        size_bytes: isRec ? Math.round((6 + Math.random() * 40) * 1e6) : Math.round((0.3 + Math.random() * 1.4) * 1e6),
        has_thumbnail: true,
      });
    }
  });
  _media.sort((a, b) => b.created_ts - a.created_ts);

  let evId = 520;
  const evCams = ["/dev/video0", "/dev/video2", "synthetic-1", "/dev/video4"];
  for (let i = 0; i < 22; i++) {
    const cam = evCams[Math.floor(Math.random() * evCams.length)];
    const start = t0 - (i * 1100 + Math.random() * 700);
    const ongoing = i === 0 && Math.random() > 0.4;
    const dur = 6 + Math.random() * 55;
    const hasRec = Math.random() > 0.5;
    _events.push({
      id: evId--, camera_id: cam, start_ts: start, end_ts: ongoing ? null : start + dur,
      peak_score: 0.3 + Math.random() * 0.69,
      recording_id: hasRec ? _media.find((m) => m.camera_id === cam && m.media_type === "recording")?.id ?? null : null,
    });
  }
  _events.sort((a, b) => b.start_ts - a.start_ts);
})();

let _system = {
  version: "1.4.2",
  tailscale_installed: true,
  tailscale_running: true,
  access_url: "http://anycam-pi.tail9c2f.ts.net",
  local_url: "http://192.168.1.42:8088",
  media_bytes: 0,
};
const recomputeStorage = () => { _system.media_bytes = _media.reduce((s, m) => s + m.size_bytes, 0); };
recomputeStorage();

// ---------------------------------------------------------------------------
// Store + pub/sub. Simulated latency on mutations.
// ---------------------------------------------------------------------------
const _subs = new Set();
const _bump = () => _subs.forEach((fn) => fn());
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

const api = {
  subscribe(fn) { _subs.add(fn); return () => _subs.delete(fn); },
  cameras: () => _cameras.map((c) => ({ ...c })),
  camera: (id) => { const c = _cameras.find((x) => x.id === id); return c ? { ...c } : null; },
  system: () => ({ ..._system }),

  media: ({ camera_id, media_type, limit = 50, offset = 0 } = {}) => {
    let rows = _media;
    if (camera_id) rows = rows.filter((m) => m.camera_id === camera_id);
    if (media_type) rows = rows.filter((m) => m.media_type === media_type);
    return rows.slice(offset, offset + limit).map((m) => ({ ...m }));
  },
  events: ({ camera_id, limit = 50, offset = 0 } = {}) => {
    let rows = _events;
    if (camera_id) rows = rows.filter((e) => e.camera_id === camera_id);
    return rows.slice(offset, offset + limit).map((e) => ({ ...e }));
  },

  async refresh() {
    await delay(900);
    _cameras = _cameras.map((c) => ({ ...c, fps: c.status === "offline" ? 0 : c.properties.fps - 0.2 + Math.random() * 0.4 }));
    _bump();
    return api.cameras();
  },
  async patchCamera(id, update) {
    await delay(420);
    if (Math.random() < 0.07) throw new Error("Device busy — settings not applied");
    const c = _cameras.find((x) => x.id === id);
    if (!c) throw new Error("not found");
    if (update.name !== undefined) c.name = update.name;
    if (update.motion_enabled !== undefined) c.motion_enabled = update.motion_enabled;
    if (update.transform) c.transform = { ...c.transform, ...update.transform };
    if (update.properties) {
      c.properties = { ...c.properties, ...update.properties };
      if (update.properties.width) c.width = update.properties.width;
      if (update.properties.height) c.height = update.properties.height;
      if (update.properties.fps) c.fps = update.properties.fps;
    }
    _bump();
    return { ...c };
  },
  async snapshot(id) {
    await delay(380);
    const mid = _mediaSeq--;
    _media.unshift({ id: mid, camera_id: id, media_type: "snapshot", created_ts: NOW(), trigger: "manual", size_bytes: Math.round((0.4 + Math.random()) * 1e6), has_thumbnail: true });
    recomputeStorage(); _bump();
    return { ok: true, media_id: mid };
  },
  async startRecording(id) {
    await delay(300);
    const c = _cameras.find((x) => x.id === id); if (c) c.recording = true;
    _bump();
    return { ok: true, detail: "recording started" };
  },
  async stopRecording(id) {
    await delay(300);
    const c = _cameras.find((x) => x.id === id); if (c) c.recording = false;
    const mid = _mediaSeq--;
    _media.unshift({ id: mid, camera_id: id, media_type: "recording", created_ts: NOW(), trigger: "manual", size_bytes: Math.round((4 + Math.random() * 20) * 1e6), has_thumbnail: true });
    recomputeStorage(); _bump();
    return { ok: true, media_id: mid };
  },
  async deleteMedia(mid) {
    await delay(300);
    _media = _media.filter((m) => m.id !== mid);
    recomputeStorage(); _bump();
    return { ok: true };
  },
};

// Background life: jitter fps + occasionally add a motion event so feeds feel alive.
setInterval(() => {
  _cameras.forEach((c) => { if (c.status !== "offline") c.fps = Math.max(0.5, c.properties.fps - 0.6 + Math.random() * 1.0); });
  _bump();
}, 2600);
setInterval(() => {
  if (document.hidden) return;
  if (Math.random() > 0.6) {
    const cands = _cameras.filter((c) => c.motion_enabled && c.status !== "offline");
    if (!cands.length) return;
    const c = cands[Math.floor(Math.random() * cands.length)];
    _events.unshift({ id: 600 + Math.floor(Math.random() * 9000), camera_id: c.id, start_ts: NOW(), end_ts: null, peak_score: 0.4 + Math.random() * 0.55, recording_id: null });
    if (_events.length > 60) _events.pop();
    _bump();
  }
}, 9000);

// ---------------------------------------------------------------------------
// Polling hooks (visibility-aware — pause when tab hidden). React-Query-ish.
// ---------------------------------------------------------------------------
function usePageVisible() {
  const [vis, setVis] = useState(!document.hidden);
  useEffect(() => {
    const on = () => setVis(!document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return vis;
}

// Re-renders on store changes; `interval` triggers a soft re-read while visible.
function useStore(selector, interval) {
  const [, force] = useState(0);
  const visible = usePageVisible();
  useEffect(() => api.subscribe(() => force((n) => n + 1)), []);
  useEffect(() => {
    if (!interval || !visible) return;
    const t = setInterval(() => force((n) => n + 1), interval);
    return () => clearInterval(t);
  }, [interval, visible]);
  return selector();
}

const useCameras = () => useStore(() => api.cameras(), 2500);
const useCamera = (id) => useStore(() => api.camera(id), 2500);
const useSystem = () => useStore(() => api.system(), 15000);
const useEventsFeed = (filter) => useStore(() => api.events(filter), 5000);

// ---------------------------------------------------------------------------
// Synthetic feed renderer — draws a believable security-cam scene to a canvas.
// Returns a motion score (0..1) for the live motion indicator.
// ---------------------------------------------------------------------------
function _lerpColor(a, b, t) {
  const pa = a.match(/\w\w/g).map((h) => parseInt(h, 16));
  const pb = b.match(/\w\w/g).map((h) => parseInt(h, 16));
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function renderFeed(canvas, cam, opts) {
  const { t = 0, zoom = 1, panX = 0.5, panY = 0.5, brightness = 50, contrast = 50, saturation = 50, rotation = 0, flipH = false, flipV = false, glitch = false, dim = false } = opts || {};
  const scene = SCENES[cam.id] || SCENES["synthetic-0"];
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.save();
  ctx.clearRect(0, 0, W, H);

  // transform: rotation/flip (global camera transform) then zoom/pan (local view).
  ctx.translate(W / 2, H / 2);
  if (rotation) ctx.rotate((rotation * Math.PI) / 180);
  ctx.scale(flipH ? -1 : 1, flipV ? -1 : 1);
  const z = zoom;
  ctx.scale(z, z);
  ctx.translate(-(panX - 0.5) * W * 1.4, -(panY - 0.5) * H * 1.4);
  ctx.translate(-W / 2, -H / 2);

  let motion = 0;

  if (scene.kind === "pattern") {
    drawTestPattern(ctx, W, H, t);
  } else {
    // sky gradient
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, scene.sky[0]); g.addColorStop(1, scene.sky[1]);
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    const horizon = H * 0.62;
    // ground
    ctx.fillStyle = scene.ground; ctx.fillRect(0, horizon, W, H - horizon);
    ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, horizon); ctx.lineTo(W, horizon); ctx.stroke();

    if (scene.kind === "porch") drawPorch(ctx, W, H, horizon);
    if (scene.kind === "yard") drawYard(ctx, W, H, horizon);
    if (scene.kind === "desk") drawDesk(ctx, W, H, horizon);
    if (scene.kind === "garage") drawGarage(ctx, W, H, horizon);
    if (scene.kind === "crib") drawCrib(ctx, W, H, horizon);

    // moving subject -> motion
    if (scene.subject !== "none") {
      const cyc = (t * scene.speed) % 1;
      const visible = cyc < 0.5;
      if (visible) {
        const p = cyc / 0.5; // 0..1 across frame
        const sx = p * W;
        const sy = horizon + (H - horizon) * 0.45;
        ctx.save();
        ctx.fillStyle = "rgba(8,10,14,0.82)";
        if (scene.subject === "person") {
          const s = H * 0.16;
          ctx.beginPath(); ctx.ellipse(sx, sy, s * 0.28, s * 0.5, 0, 0, 7); ctx.fill();
          ctx.beginPath(); ctx.arc(sx, sy - s * 0.6, s * 0.2, 0, 7); ctx.fill();
        } else {
          const s = H * 0.08;
          ctx.beginPath(); ctx.ellipse(sx, sy + s, s * 0.9, s * 0.5, 0, 0, 7); ctx.fill();
          ctx.beginPath(); ctx.arc(sx + s * 0.8, sy + s * 0.6, s * 0.35, 0, 7); ctx.fill();
        }
        ctx.restore();
        const center = Math.abs(p - 0.5);
        motion = Math.max(0, 1 - center * 2) * (0.5 + 0.5 * Math.sin(t * 6));
        motion = Math.min(1, 0.4 + motion * 0.6);
      }
    }
  }
  ctx.restore();

  // ---- post overlays drawn in screen space (not transformed) ----
  // brightness / contrast / saturation as translucent veils (visual approximation)
  if (brightness !== 50) {
    ctx.fillStyle = brightness > 50 ? `rgba(255,255,255,${(brightness - 50) / 200})` : `rgba(0,0,0,${(50 - brightness) / 130})`;
    ctx.fillRect(0, 0, W, H);
  }
  if (contrast > 50) { ctx.globalCompositeOperation = "overlay"; ctx.fillStyle = `rgba(60,60,60,${(contrast - 50) / 160})`; ctx.fillRect(0, 0, W, H); ctx.globalCompositeOperation = "source-over"; }

  // scanlines
  ctx.fillStyle = "rgba(0,0,0,0.06)";
  for (let y = 0; y < H; y += 3) ctx.fillRect(0, y, W, 1);

  // sparse noise
  const grain = 60 + (dim ? 0 : 90);
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  for (let i = 0; i < grain; i++) ctx.fillRect((Math.random() * W) | 0, (Math.random() * H) | 0, 1, 1);

  // glitch bars (degraded)
  if (glitch && Math.random() > 0.5) {
    const gy = Math.random() * H;
    ctx.fillStyle = "rgba(120,140,200,0.10)";
    ctx.fillRect(0, gy, W, 2 + Math.random() * 8);
  }

  // vignette
  const v = ctx.createRadialGradient(W / 2, H / 2, H * 0.3, W / 2, H / 2, H * 0.85);
  v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,0.45)");
  ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);

  if (dim) { ctx.fillStyle = "rgba(8,10,14,0.55)"; ctx.fillRect(0, 0, W, H); }

  return motion;
}

function drawPorch(ctx, W, H, hz) {
  // house wall + door + porch light
  ctx.fillStyle = "rgba(40,44,54,0.9)"; ctx.fillRect(W * 0.05, hz - H * 0.42, W * 0.55, H * 0.42);
  ctx.fillStyle = "#11141a"; ctx.fillRect(W * 0.16, hz - H * 0.34, W * 0.14, H * 0.34); // door
  ctx.fillStyle = "rgba(245,166,35,0.55)"; ctx.beginPath(); ctx.arc(W * 0.36, hz - H * 0.3, H * 0.05, 0, 7); ctx.fill(); // light
  ctx.fillStyle = "rgba(245,166,35,0.10)"; ctx.beginPath(); ctx.arc(W * 0.36, hz - H * 0.3, H * 0.18, 0, 7); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.04)"; ctx.fillRect(W * 0.63, hz - H * 0.5, W * 0.3, H * 0.5); // wall edge
}
function drawYard(ctx, W, H, hz) {
  ctx.strokeStyle = "rgba(60,70,80,0.8)"; ctx.lineWidth = 3; // fence
  for (let x = W * 0.05; x < W * 0.95; x += W * 0.07) { ctx.beginPath(); ctx.moveTo(x, hz); ctx.lineTo(x, hz - H * 0.16); ctx.stroke(); }
  ctx.beginPath(); ctx.moveTo(W * 0.05, hz - H * 0.1); ctx.lineTo(W * 0.95, hz - H * 0.1); ctx.stroke();
  ctx.fillStyle = "rgba(20,30,18,0.95)"; ctx.beginPath(); ctx.arc(W * 0.8, hz - H * 0.2, H * 0.22, 0, 7); ctx.fill(); // tree
  ctx.fillStyle = "#2a1d12"; ctx.fillRect(W * 0.79, hz - H * 0.2, W * 0.02, H * 0.2);
}
function drawDesk(ctx, W, H, hz) {
  ctx.fillStyle = "rgba(35,38,47,0.95)"; ctx.fillRect(0, hz - H * 0.06, W, H * 0.06); // desk edge
  ctx.fillStyle = "#0a0c10"; ctx.fillRect(W * 0.3, hz - H * 0.34, W * 0.4, H * 0.26); // monitor
  ctx.fillStyle = "rgba(79,140,255,0.18)"; ctx.fillRect(W * 0.32, hz - H * 0.32, W * 0.36, H * 0.22);
  ctx.fillStyle = "rgba(255,255,255,0.05)"; ctx.fillRect(W * 0.1, hz - H * 0.12, W * 0.12, H * 0.06); // keyboard-ish
}
function drawGarage(ctx, W, H, hz) {
  ctx.fillStyle = "rgba(30,33,40,0.95)"; ctx.fillRect(W * 0.1, hz - H * 0.4, W * 0.8, H * 0.4); // door panels
  ctx.strokeStyle = "rgba(0,0,0,0.5)"; ctx.lineWidth = 2;
  for (let y = hz - H * 0.4; y < hz; y += H * 0.1) { ctx.beginPath(); ctx.moveTo(W * 0.1, y); ctx.lineTo(W * 0.9, y); ctx.stroke(); }
}
function drawCrib(ctx, W, H, hz) {
  ctx.strokeStyle = "rgba(120,100,140,0.7)"; ctx.lineWidth = 4; // crib bars
  for (let x = W * 0.2; x < W * 0.8; x += W * 0.06) { ctx.beginPath(); ctx.moveTo(x, hz); ctx.lineTo(x, hz - H * 0.22); ctx.stroke(); }
  ctx.fillStyle = "rgba(180,140,200,0.25)"; ctx.beginPath(); ctx.arc(W * 0.85, H * 0.2, H * 0.06, 0, 7); ctx.fill(); // night light
}
function drawTestPattern(ctx, W, H) {
  const bars = ["#c8c8c8", "#c8c800", "#00c8c8", "#00c800", "#c800c8", "#c80000", "#0000c8"];
  const bw = W / bars.length;
  bars.forEach((c, i) => { ctx.fillStyle = c; ctx.fillRect(i * bw, 0, bw + 1, H * 0.66); });
  ctx.fillStyle = "#111"; ctx.fillRect(0, H * 0.66, W, H * 0.34);
  ctx.fillStyle = "#1d2330"; ctx.fillRect(W * 0.32, H * 0.7, W * 0.36, H * 0.22);
  ctx.strokeStyle = "rgba(79,140,255,0.6)"; ctx.lineWidth = 2;
  ctx.strokeRect(W * 0.32, H * 0.7, W * 0.36, H * 0.22);
}

// timestamp OSD string (burned-in look)
function osdStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

Object.assign(window, {
  streamUrl, snapshotUrl, mediaFileUrl, mediaThumbUrl, cacheBust,
  api, camName, useCameras, useCamera, useSystem, useEventsFeed, usePageVisible, useStore,
  renderFeed, osdStamp, SCENES,
});
