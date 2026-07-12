/**
 * popup.js — TailCam Companion toolbar popup.
 *
 * Shows the configured nodes (tabs when more than one), the active node's
 * cameras with live snapshot thumbnails (refreshed every 3s while visible)
 * and per-camera actions, plus the most recent motion events. Opening the
 * popup marks events as seen so the background badge clears.
 */

import { openOptionsPage, sendMessage, tabsCreate } from "../lib/compat.js";
import { createNode } from "../lib/api.js";
import { loadSettings, loadState } from "../lib/settings.js";
import { labelChip, relativeTime } from "../lib/format.js";

const THUMB_REFRESH_MS = 3000;
const EVENTS_SHOWN = 8;
const SVG_NS = "http://www.w3.org/2000/svg";

/** Inline icon paths (24x24 viewBox, filled with currentColor). */
const ICON_PATHS = {
  camera:
    "M9 4 7.8 6H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-2.8L15 4H9zm3 4.5A4.5 4.5 0 1 1 7.5 13 4.5 4.5 0 0 1 12 8.5zm0 2A2.5 2.5 0 1 0 14.5 13 2.5 2.5 0 0 0 12 10.5z",
  record: "M12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10z",
  stop: "M7.5 7.5h9v9h-9z",
  eye: "M12 5.5c-5 0-8.6 4.2-9.8 6 1.2 1.8 4.8 6 9.8 6s8.6-4.2 9.8-6c-1.2-1.8-4.8-6-9.8-6zm0 2.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7z",
  external:
    "M14 4h6v6h-2V7.4l-7.3 7.3-1.4-1.4L16.6 6H14V4zM5 6h6v2H7v9h9v-4h2v6H5V6z",
  check: "M9.5 16.2 5.8 12.5l-1.4 1.4 5.1 5.1 10-10-1.4-1.4z",
};

const els = {
  nodeTabs: document.getElementById("node-tabs"),
  empty: document.getElementById("empty"),
  emptyAdd: document.getElementById("empty-add"),
  banner: document.getElementById("banner"),
  loading: document.getElementById("loading"),
  camerasSection: document.getElementById("cameras-section"),
  cameraList: document.getElementById("camera-list"),
  eventsSection: document.getElementById("events-section"),
  eventList: document.getElementById("event-list"),
  foot: document.getElementById("foot"),
  linkDashboard: document.getElementById("link-dashboard"),
  linkOptions: document.getElementById("link-options"),
  toast: document.getElementById("toast"),
};

/** @type {import("../lib/settings.js").Settings|null} */
let settings = null;
/** Active node config + API client. */
let activeNodeCfg = null;
let api = null;
/** Live-thumbnail registry for the currently rendered camera list. */
let thumbEntries = [];
let thumbTimer = null;
let toastTimer = null;
/** Increments on node switch so stale fetches never render. */
let renderEpoch = 0;

/* ------------------------------------------------------------------ */
/* tiny DOM helpers                                                    */
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
 * Show a transient toast message.
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

/* ------------------------------------------------------------------ */
/* action-button feedback (spinner -> check / error toast)             */
/* ------------------------------------------------------------------ */

/**
 * Run an async camera action behind a button, showing a spinner while it
 * runs and a brief check mark on success. Errors surface as a toast.
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
/* thumbnails                                                          */
/* ------------------------------------------------------------------ */

/** Refresh every registered live thumbnail with a cache-busted snapshot. */
function refreshThumbs() {
  for (const { img, camera } of thumbEntries) {
    img.src = api.snapshotUrl(camera);
  }
}

/** Start the 3s thumbnail refresh loop (idempotent). */
function startThumbLoop() {
  if (thumbTimer || !thumbEntries.length) return;
  thumbTimer = setInterval(refreshThumbs, THUMB_REFRESH_MS);
}

/** Stop the thumbnail refresh loop. */
function stopThumbLoop() {
  clearInterval(thumbTimer);
  thumbTimer = null;
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopThumbLoop();
  else startThumbLoop();
});

/* ------------------------------------------------------------------ */
/* camera rows                                                         */
/* ------------------------------------------------------------------ */

/**
 * Status LED metadata for a camera.
 * @param {Object} camera CameraInfo
 * @returns {{cls: string, label: string, title: string}}
 */
function cameraStatus(camera) {
  const status = camera.status || "offline";
  if (status === "offline" || status === "error") {
    return {
      cls: "led err",
      label: status,
      title: camera.last_error || `Camera ${status}`,
    };
  }
  if (status === "starting" || status === "connecting") {
    return { cls: "led warn", label: status, title: `Camera ${status}` };
  }
  return { cls: "led", label: status, title: `Camera ${status}` };
}

/**
 * Small icon button for a camera row.
 * @param {string} icon ICON_PATHS key
 * @param {string} title tooltip / aria-label
 * @param {() => void} onClick
 * @returns {HTMLButtonElement}
 */
function iconButton(icon, title, onClick) {
  const btn = el("button", "icon-btn");
  btn.type = "button";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.append(svgIcon(icon));
  btn.addEventListener("click", onClick);
  return btn;
}

/**
 * Apply record-toggle appearance for the camera's current recording state.
 * @param {HTMLButtonElement} btn
 * @param {Object} camera
 */
function styleRecordButton(btn, camera) {
  btn.classList.toggle("rec-on", Boolean(camera.recording));
  const title = camera.recording ? "Stop recording" : "Start recording";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.replaceChildren(svgIcon(camera.recording ? "stop" : "record"));
}

/**
 * Render one camera row (thumbnail, status, actions).
 * @param {Object} camera CameraInfo
 * @returns {HTMLElement}
 */
function renderCameraRow(camera) {
  const row = el("div", "cam-row");

  // Thumbnail (live snapshot, refreshed while the popup is open).
  const thumb = el("div", "cam-thumb");
  const img = el("img");
  img.alt = "";
  img.decoding = "async";
  img.addEventListener("error", () => thumb.classList.add("dead"));
  img.addEventListener("load", () => thumb.classList.remove("dead"));
  const fallback = el("span", "thumb-fallback");
  fallback.append(svgIcon("camera"));
  thumb.append(img, fallback);
  const offline = camera.status === "offline" || camera.status === "error";
  if (offline) {
    thumb.classList.add("dead");
  } else {
    img.src = api.snapshotUrl(camera);
    thumbEntries.push({ img, camera });
  }

  // Name + status + sub line.
  const body = el("div", "cam-body");
  const nameLine = el("div", "cam-name-line");
  const status = cameraStatus(camera);
  const led = el("span", status.cls);
  led.title = status.title;
  nameLine.append(led, el("span", "cam-name", camera.name || camera.id));
  const sub = el("div", "cam-sub");
  sub.append(status.label);
  if (camera.recording) {
    sub.append(" · ");
    sub.append(el("span", "rec-chip", "rec"));
  }
  if (camera.host) {
    sub.append(` · ${camera.host}`);
  }
  body.append(nameLine, sub);

  // Actions.
  const actions = el("div", "cam-actions");
  const snapBtn = iconButton("camera", "Take snapshot", () =>
    runAction(snapBtn, () => api.snapshot(camera)),
  );

  const recBtn = iconButton("record", "", () =>
    runAction(recBtn, async () => {
      try {
        if (camera.recording) await api.stopRecording(camera);
        else await api.startRecording(camera);
        camera.recording = !camera.recording;
      } catch (err) {
        if (err?.status === 409) {
          // 409 is state info: the camera was already in the target state.
          camera.recording = !camera.recording;
        } else {
          throw err;
        }
      }
      styleRecordButton(recBtn, camera);
      renderSubLine();
    }),
  );
  styleRecordButton(recBtn, camera);

  const glanceBtn = iconButton("eye", "Glance (floating viewer)", async () => {
    await sendMessage({
      type: "openGlance",
      nodeId: activeNodeCfg.id,
      cameraId: camera.id,
    }).catch(() => {});
    window.close();
  });

  const dashBtn = iconButton("external", "Open in dashboard", async () => {
    await tabsCreate({ url: api.cameraDashboardUrl(camera) });
    window.close();
  });

  actions.append(snapBtn, recBtn, glanceBtn, dashBtn);
  body.append(actions);

  /** Re-render the status sub line after a recording toggle. */
  function renderSubLine() {
    sub.replaceChildren(status.label);
    if (camera.recording) {
      sub.append(" · ");
      sub.append(el("span", "rec-chip", "rec"));
    }
    if (camera.host) sub.append(` · ${camera.host}`);
  }

  row.append(thumb, body);
  return row;
}

/* ------------------------------------------------------------------ */
/* event rows                                                          */
/* ------------------------------------------------------------------ */

/**
 * Render one recent-event row; clicking opens the node's events page.
 * @param {Object} event MotionEventInfo
 * @param {Array} cameras CameraInfo[] used to resolve the camera name
 * @returns {HTMLElement}
 */
function renderEventRow(event, cameras) {
  const row = el("button", "event-row");
  row.type = "button";

  if (event.has_thumb) {
    const img = el("img", "event-thumb");
    img.alt = "";
    img.decoding = "async";
    img.src = api.eventThumbUrl(event);
    img.addEventListener("error", () => {
      const ph = el("span", "event-thumb placeholder", labelChip(event.label));
      img.replaceWith(ph);
    });
    row.append(img);
  } else {
    row.append(el("span", "event-thumb placeholder", labelChip(event.label)));
  }

  const camera = cameras.find(
    (c) => c.id === event.camera_id && (c.host || "") === (event.host || ""),
  ) ?? cameras.find((c) => c.id === event.camera_id);
  const cameraName = camera?.name || event.camera_id;
  const label = event.label
    ? event.label.charAt(0).toUpperCase() + event.label.slice(1)
    : "Motion";

  const body = el("div", "event-body");
  const title = el("div", "event-title");
  title.append(el("span", "chip", labelChip(event.label)), label);
  body.append(title, el("div", "event-sub", event.description || cameraName));
  row.append(body, el("span", "event-time", relativeTime(event.start_ts)));

  row.addEventListener("click", async () => {
    await tabsCreate({ url: api.dashboardUrl("/events") });
    window.close();
  });
  return row;
}

/* ------------------------------------------------------------------ */
/* node selection + data load                                          */
/* ------------------------------------------------------------------ */

/** Render the node tab strip (hidden when only one node is configured). */
function renderNodeTabs() {
  if (settings.nodes.length < 2) {
    els.nodeTabs.hidden = true;
    return;
  }
  els.nodeTabs.replaceChildren();
  for (const node of settings.nodes) {
    const tab = el("button", "node-tab", node.name || node.url);
    tab.type = "button";
    tab.classList.toggle("active", node.id === activeNodeCfg?.id);
    tab.addEventListener("click", () => selectNode(node));
    els.nodeTabs.append(tab);
  }
  els.nodeTabs.hidden = false;
}

/**
 * Switch the popup to a node: fetch its cameras + events and render.
 * Falls back to the background's cached camera list when unreachable.
 * @param {import("../lib/settings.js").NodeConfig} nodeCfg
 */
async function selectNode(nodeCfg) {
  const epoch = ++renderEpoch;
  activeNodeCfg = nodeCfg;
  api = createNode(nodeCfg);
  stopThumbLoop();
  thumbEntries = [];
  renderNodeTabs();

  els.banner.hidden = true;
  els.camerasSection.hidden = true;
  els.eventsSection.hidden = true;
  els.loading.hidden = false;

  const [camerasRes, eventsRes] = await Promise.allSettled([
    api.cameras(),
    api.events({ limit: EVENTS_SHOWN }),
  ]);
  if (epoch !== renderEpoch) return; // user switched nodes mid-flight
  els.loading.hidden = true;

  let cameras = null;
  if (camerasRes.status === "fulfilled") {
    cameras = camerasRes.value;
  } else {
    const state = await loadState();
    if (epoch !== renderEpoch) return;
    cameras = state.cachedCameras[nodeCfg.id] ?? null;
    els.banner.textContent = cameras
      ? "Node unreachable — showing cached cameras."
      : `Node unreachable (${describeError(camerasRes.reason)})`;
    els.banner.hidden = false;
  }

  els.cameraList.replaceChildren();
  if (cameras && cameras.length) {
    for (const camera of cameras) {
      els.cameraList.append(renderCameraRow(camera));
    }
    startThumbLoop();
  } else if (cameras) {
    els.cameraList.append(el("div", "list-note", "No cameras on this node."));
  }
  els.camerasSection.hidden = cameras === null;

  els.eventList.replaceChildren();
  if (eventsRes.status === "fulfilled") {
    const events = eventsRes.value.slice(0, EVENTS_SHOWN);
    if (events.length) {
      for (const event of events) {
        els.eventList.append(renderEventRow(event, cameras ?? []));
      }
    } else {
      els.eventList.append(el("div", "list-note", "No motion events yet."));
    }
    els.eventsSection.hidden = false;
  } else if (cameras !== null) {
    els.eventList.append(el("div", "list-note", "Events unavailable."));
    els.eventsSection.hidden = false;
  }
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

/** Initialize the popup from stored settings. */
async function boot() {
  settings = await loadSettings();

  els.emptyAdd.addEventListener("click", () => {
    openOptionsPage();
    window.close();
  });
  els.linkOptions.addEventListener("click", () => {
    openOptionsPage();
    window.close();
  });
  els.linkDashboard.addEventListener("click", async () => {
    if (!api) return;
    await tabsCreate({ url: api.dashboardUrl("/") });
    window.close();
  });

  if (!settings.nodes.length) {
    els.empty.hidden = false;
    return;
  }
  els.foot.hidden = false;

  // Clear the unseen badge: opening the popup counts as seeing the events.
  sendMessage({ type: "markSeen" }).catch(() => {});

  await selectNode(settings.nodes[0]);
}

boot().catch((err) => {
  els.loading.hidden = true;
  showToast(describeError(err), "error");
});
