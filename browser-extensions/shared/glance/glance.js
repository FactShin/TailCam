/**
 * glance.js — chromeless mini viewer for a single camera.
 *
 * Opened by the background as glance/glance.html?node=<id>&camera=<id>
 * (both query values encodeURIComponent'd; URLSearchParams decodes them).
 * Shows the camera's full-bleed MJPEG stream with a name overlay,
 * snapshot / record controls, an error-and-retry state, and — where the
 * Document Picture-in-Picture API exists (Chromium) — a "float" button.
 */

import { openOptionsPage } from "../lib/compat.js";
import { createNode } from "../lib/api.js";
import { loadSettings, loadState } from "../lib/settings.js";

const CONNECT_TIMEOUT_MS = 12000;
const CONNECT_POLL_MS = 250;
const RETRY_DELAY_S = 6;
/** A 'load' on an already-connected MJPEG <img> this long after the last
 *  one means the stream closed (Chrome fires 'load' at end of response). */
const STREAM_END_GAP_MS = 3000;
const STREAM_OPTS = { fps: 15, w: 1280, q: 80 };
const SVG_NS = "http://www.w3.org/2000/svg";

/** Inline icon paths (24x24 viewBox, evenodd fill with currentColor). */
const ICON_PATHS = {
  camera:
    "M9 4 7.8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-2.8L15 4H9zm3 4.5A4.5 4.5 0 1 1 7.5 13 4.5 4.5 0 0 1 12 8.5zm0 2A2.5 2.5 0 1 0 14.5 13 2.5 2.5 0 0 0 12 10.5z",
  record: "M12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10z",
  stop: "M7.5 7.5h9v9h-9z",
  float: "M3 5h18v14H3zm2 2v10h14V7zm7 4h6v4h-6z",
  check: "M9.5 16.2 5.8 12.5l-1.4 1.4 5.1 5.1 10-10-1.4-1.4z",
};

const els = {
  stage: document.getElementById("stage"),
  stream: document.getElementById("stream"),
  recLed: document.getElementById("rec-led"),
  camName: document.getElementById("cam-name"),
  nodeName: document.getElementById("node-name"),
  controls: document.getElementById("controls"),
  btnSnapshot: document.getElementById("btn-snapshot"),
  btnRecord: document.getElementById("btn-record"),
  btnFloat: document.getElementById("btn-float"),
  status: document.getElementById("status"),
  statusSpinner: document.getElementById("status-spinner"),
  statusText: document.getElementById("status-text"),
  btnRetry: document.getElementById("btn-retry"),
  floatingNote: document.getElementById("floating-note"),
  toast: document.getElementById("toast"),
};

let api = null;
let nodeCfg = null;
let camera = null;
let connected = false;
let lastLoadAt = 0;
let connectPoll = null;
let connectDeadline = null;
let retryTimer = null;
let toastTimer = null;
/** Document Picture-in-Picture window, when floating. */
let pipWin = null;

/* ------------------------------------------------------------------ */
/* tiny helpers                                                        */
/* ------------------------------------------------------------------ */

/**
 * Create an element with optional class and text content.
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text]
 * @returns {HTMLElement}
 */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * Build an inline SVG icon from ICON_PATHS.
 * @param {keyof typeof ICON_PATHS} name
 * @returns {SVGSVGElement}
 */
function svgIcon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICON_PATHS[name]);
  path.setAttribute("fill", "currentColor");
  path.setAttribute("fill-rule", "evenodd");
  svg.append(path);
  return svg;
}

/**
 * Show a transient toast message (travels with the stage into PiP).
 * @param {string} message
 * @param {"error"|"ok"|""} [kind]
 */
function showToast(message, kind = "") {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.className = `toast ${kind}`.trim();
  els.toast.hidden = false;
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, 3200);
}

/**
 * Human message for a failed API call.
 * @param {Error & {kind?: string, status?: number}} err
 * @returns {string}
 */
function describeError(err) {
  if (err?.kind === "blocked") {
    return "Blocked by node security policy — use the dashboard for this action.";
  }
  if (err?.kind === "network") return "Node unreachable.";
  return err?.message || "Request failed.";
}

/**
 * Run an async action behind a control button with spinner/check feedback.
 * @param {HTMLButtonElement} btn
 * @param {() => Promise<void>} action
 */
async function runAction(btn, action) {
  if (btn.disabled) return;
  const original = [...btn.childNodes];
  btn.disabled = true;
  btn.replaceChildren(el("span", "spinner"));
  try {
    await action();
    btn.replaceChildren(svgIcon("check"));
    setTimeout(() => {
      btn.replaceChildren(...original);
      btn.disabled = false;
    }, 900);
  } catch (err) {
    btn.replaceChildren(...original);
    btn.disabled = false;
    showToast(describeError(err), "error");
  }
}

/* ------------------------------------------------------------------ */
/* status panel                                                        */
/* ------------------------------------------------------------------ */

/**
 * Show the status overlay.
 * @param {{text: string, spinner?: boolean, error?: boolean, retryLabel?: string|null}} opts
 */
function showStatus({ text, spinner = false, error = false, retryLabel = null }) {
  els.statusText.textContent = text;
  els.statusSpinner.hidden = !spinner;
  els.status.classList.toggle("error", error);
  els.btnRetry.hidden = !retryLabel;
  if (retryLabel) els.btnRetry.textContent = retryLabel;
  els.status.hidden = false;
}

/** Hide the status overlay. */
function hideStatus() {
  els.status.hidden = true;
}

/**
 * Terminal error (bad node/camera): no stream, one action button.
 * @param {string} message
 * @param {{label: string, onClick: () => void}} [action]
 */
function fatal(message, action) {
  document.title = "Glance — TailCam";
  els.controls.hidden = true;
  els.stage.classList.add("disconnected");
  showStatus({ text: message, error: true, retryLabel: action?.label ?? null });
  if (action) {
    els.btnRetry.onclick = action.onClick;
  }
}

/* ------------------------------------------------------------------ */
/* stream lifecycle                                                    */
/* ------------------------------------------------------------------ */

/** Stop the connect-detection poll. */
function stopConnectPoll() {
  clearInterval(connectPoll);
  clearTimeout(connectDeadline);
  connectPoll = null;
  connectDeadline = null;
}

/** Mark the stream as live and clear overlays. */
function setConnected() {
  if (connected) return;
  connected = true;
  lastLoadAt = Date.now();
  stopConnectPoll();
  els.stage.classList.remove("disconnected");
  hideStatus();
}

/**
 * Show the disconnected state and schedule an automatic reconnect.
 * @param {string} message
 */
function streamDown(message) {
  connected = false;
  stopConnectPoll();
  clearInterval(retryTimer);
  els.stage.classList.add("disconnected");

  let remaining = RETRY_DELAY_S;
  const update = () =>
    showStatus({
      text: `${message} — retrying in ${remaining}s`,
      error: true,
      retryLabel: "Retry now",
    });
  update();
  retryTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(retryTimer);
      startStream();
    } else {
      update();
    }
  }, 1000);
}

/** (Re)connect the MJPEG stream and watch for the first frame. */
function startStream() {
  if (!api || !camera) return;
  clearInterval(retryTimer);
  stopConnectPoll();
  connected = false;
  els.stage.classList.remove("disconnected");
  showStatus({ text: "Connecting…", spinner: true });

  // Extra `_` param forces a brand-new request on every reconnect.
  els.stream.src = api.streamUrl(camera, { ...STREAM_OPTS, _: Date.now() });

  // MJPEG <img> elements paint frames without firing 'load' (Chromium), so
  // poll naturalWidth to detect the first decoded frame.
  connectPoll = setInterval(() => {
    if (els.stream.naturalWidth > 0) setConnected();
  }, CONNECT_POLL_MS);
  connectDeadline = setTimeout(() => {
    if (!connected) streamDown("No video from camera");
  }, CONNECT_TIMEOUT_MS);
}

els.stream.addEventListener("error", () => {
  if (!api) return;
  streamDown("Stream unavailable");
});

els.stream.addEventListener("load", () => {
  if (!api) return;
  if (!connected) {
    setConnected();
    return;
  }
  // Chromium fires a single 'load' when the multipart response ends;
  // Safari-style per-frame 'load' bursts are ignored via the time gap.
  const now = Date.now();
  const gap = now - lastLoadAt;
  lastLoadAt = now;
  if (gap > STREAM_END_GAP_MS) streamDown("Stream ended");
});

els.btnRetry.addEventListener("click", () => startStream());

/* ------------------------------------------------------------------ */
/* controls                                                            */
/* ------------------------------------------------------------------ */

/**
 * Apply record-toggle appearance for the camera's current recording state.
 */
function styleRecordButton() {
  els.btnRecord.classList.toggle("rec-on", Boolean(camera.recording));
  const title = camera.recording ? "Stop recording" : "Start recording";
  els.btnRecord.title = title;
  els.btnRecord.setAttribute("aria-label", title);
  els.btnRecord.replaceChildren(svgIcon(camera.recording ? "stop" : "record"));
  els.recLed.hidden = !camera.recording;
}

/** Wire up the snapshot / record / float controls. */
function setupControls() {
  els.btnSnapshot.append(svgIcon("camera"));
  els.btnSnapshot.addEventListener("click", () =>
    runAction(els.btnSnapshot, () => api.snapshot(camera)),
  );

  styleRecordButton();
  els.btnRecord.addEventListener("click", () =>
    runAction(els.btnRecord, async () => {
      try {
        if (camera.recording) await api.stopRecording(camera);
        else await api.startRecording(camera);
        camera.recording = !camera.recording;
      } catch (err) {
        if (err?.status === 409) {
          // 409 is state info: camera was already in the target state.
          camera.recording = !camera.recording;
        } else {
          throw err;
        }
      }
      styleRecordButton();
    }),
  );

  // Document Picture-in-Picture is Chromium-only; feature-detect.
  if ("documentPictureInPicture" in window) {
    els.btnFloat.append(svgIcon("float"));
    els.btnFloat.hidden = false;
    els.btnFloat.addEventListener("click", toggleFloat);
  }

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") window.close();
  });
}

/**
 * Move the stage into (or back out of) a Document Picture-in-Picture
 * window so the stream stays on top of other windows.
 */
async function toggleFloat() {
  if (pipWin) {
    pipWin.close();
    return;
  }
  let win;
  try {
    win = await window.documentPictureInPicture.requestWindow({
      width: window.innerWidth || 480,
      height: window.innerHeight || 300,
    });
  } catch {
    showToast("Floating window unavailable.", "error");
    return;
  }
  pipWin = win;
  for (const sheet of document.querySelectorAll('link[rel="stylesheet"]')) {
    const link = win.document.createElement("link");
    link.rel = "stylesheet";
    link.href = sheet.href; // .href is already absolute
    win.document.head.append(link);
  }
  win.document.title = document.title;
  win.document.body.append(els.stage);
  els.floatingNote.hidden = false;
  win.addEventListener("pagehide", () => {
    document.body.prepend(els.stage);
    els.floatingNote.hidden = true;
    pipWin = null;
  });
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

/**
 * Find the camera on the node: prefer a live fetch (fresh recording
 * state), fall back to the background's cached list when unreachable.
 * @param {string} cameraId
 * @returns {Promise<Object|null>} CameraInfo or null
 */
async function resolveCamera(cameraId) {
  try {
    const cameras = await api.cameras();
    const found = cameras.find((c) => c.id === cameraId);
    if (found) return found;
  } catch {
    /* fall through to cache */
  }
  const state = await loadState();
  return (state.cachedCameras[nodeCfg.id] ?? []).find((c) => c.id === cameraId) ?? null;
}

/** Parse the query string, resolve node + camera, start the stream. */
async function boot() {
  const params = new URLSearchParams(location.search);
  const nodeId = params.get("node") ?? "";
  const cameraId = params.get("camera") ?? "";

  const settings = await loadSettings();
  nodeCfg = settings.nodes.find((n) => n.id === nodeId) ?? null;
  if (!nodeCfg) {
    fatal("This TailCam node is no longer configured.", {
      label: "Open options",
      onClick: () => {
        openOptionsPage();
        window.close();
      },
    });
    return;
  }
  api = createNode(nodeCfg);

  camera = await resolveCamera(cameraId);
  if (!camera) {
    fatal(`Camera not found on ${nodeCfg.name || nodeCfg.url}.`, {
      label: "Open options",
      onClick: () => {
        openOptionsPage();
        window.close();
      },
    });
    api = null; // disable stream event handlers
    return;
  }

  const name = camera.name || camera.id;
  document.title = `${name} — TailCam`;
  els.camName.textContent = name;
  els.nodeName.textContent = nodeCfg.name || nodeCfg.url;

  setupControls();
  startStream();
}

boot().catch((err) => {
  fatal(describeError(err));
});
