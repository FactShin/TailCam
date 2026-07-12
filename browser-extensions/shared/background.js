/**
 * background.js — MV3 service-worker-safe background module.
 *
 * Responsibilities: event polling via ext.alarms (badge + notifications),
 * omnibox "tc" keyword, keyboard commands, action context menu, and the
 * runtime message API used by the popup/options pages
 * ({type: "markSeen"|"getStatus"|"settingsChanged"|"openGlance"}).
 *
 * The worker can be shut down at any time: every handler re-reads settings
 * and state from ext.storage.local; nothing is kept in module globals.
 */

import {
  ext,
  alarmsCreate,
  notificationsCreate,
  notificationsClear,
  openOptionsPage,
  runtimeUrl,
  setBadgeBackgroundColor,
  setBadgeText,
  tabsCreate,
  windowsCreate,
} from "./lib/compat.js";
import { createNode } from "./lib/api.js";
import {
  loadSettings,
  loadState,
  markNodeSeen,
  pruneRemovedNodes,
  rememberNotified,
  updateState,
} from "./lib/settings.js";
import { isInQuietHours, labelChip, relativeTime } from "./lib/format.js";

const POLL_ALARM = "tailcam-poll";
const BADGE_COLOR = "#5b7fff";
const EVENTS_PER_POLL = 30;
const NOTIFY_CAP_PER_CYCLE = 3;
const NOTIFICATION_PREFIX = "tc-evt|";

const MENU_DASHBOARD = "tc-open-dashboard";
const MENU_GLANCE = "tc-glance-pinned";
const MENU_OPTIONS = "tc-options";

/* ------------------------------------------------------------------ */
/* setup                                                               */
/* ------------------------------------------------------------------ */

/**
 * (Re)create the poll alarm from settings and refresh static UI chrome.
 * Safe to call repeatedly (alarms.create replaces by name).
 */
async function init() {
  const settings = await loadSettings();
  await setBadgeBackgroundColor(BADGE_COLOR);
  await schedulePolling(settings);
  buildContextMenus();
  if (ext.omnibox) {
    ext.omnibox.setDefaultSuggestion({
      description: "Open the TailCam dashboard",
    });
  }
  await pollAll();
}

/**
 * Create the recurring poll alarm (minimum 30s period).
 * @param {import("./lib/settings.js").Settings} settings
 */
function schedulePolling(settings) {
  const minutes = Math.max(0.5, (settings.pollSeconds || 60) / 60);
  return alarmsCreate(POLL_ALARM, { periodInMinutes: minutes });
}

/** Build the action-icon context menu (no extra host permissions needed). */
function buildContextMenus() {
  if (!ext.contextMenus) return;
  const swallow = () => void ext.runtime.lastError;
  Promise.resolve(ext.contextMenus.removeAll()).then(() => {
    const items = [
      { id: MENU_DASHBOARD, title: "Open dashboard" },
      { id: MENU_GLANCE, title: "Glance pinned camera" },
      { id: MENU_OPTIONS, title: "Options" },
    ];
    for (const item of items) {
      ext.contextMenus.create({ ...item, contexts: ["action"] }, swallow);
    }
  }, swallow);
}

/* ------------------------------------------------------------------ */
/* polling: badge + notifications                                      */
/* ------------------------------------------------------------------ */

/**
 * Whether an event qualifies for the configured notification mode.
 * @param {Object} event MotionEventInfo
 * @param {"off"|"all"|"labeled"|"person"} mode
 * @returns {boolean}
 */
function eventMatchesMode(event, mode) {
  if (mode === "all") return true;
  if (mode === "labeled") return Boolean(event.label);
  if (mode === "person") {
    return Boolean(event.label) && event.label.toLowerCase().includes("person");
  }
  return false;
}

/**
 * Fetch events + cameras for one node; per-node failures are swallowed
 * (an unreachable node must not break polling of the others).
 * @param {{id: string, name: string, url: string}} nodeCfg
 * @returns {Promise<{nodeCfg: Object, events: Array|null, cameras: Array|null}>}
 */
async function fetchNode(nodeCfg) {
  const api = createNode(nodeCfg);
  const [events, cameras] = await Promise.allSettled([
    api.events({ limit: EVENTS_PER_POLL }),
    api.cameras(),
  ]);
  return {
    nodeCfg,
    events: events.status === "fulfilled" ? events.value : null,
    cameras: cameras.status === "fulfilled" ? cameras.value : null,
  };
}

/**
 * One poll cycle: refresh cached cameras, compute unseen counts per node,
 * update the badge, and fire notifications for new events.
 */
async function pollAll() {
  const settings = await loadSettings();
  if (!settings.nodes.length) {
    await setBadgeText("");
    return;
  }

  const prev = await loadState();
  const results = await Promise.all(settings.nodes.map(fetchNode));

  const quiet = isInQuietHours(
    new Date(),
    settings.quietStart,
    settings.quietEnd,
  );
  const known = new Set(prev.notifiedEventKeys);
  /** @type {Array<{nodeCfg: Object, event: Object, key: string, cameras: Array|null}>} */
  const candidates = [];
  const handledKeys = [];

  const state = await updateState((s) => {
    pruneRemovedNodes(s, settings);
    for (const { nodeCfg, events, cameras } of results) {
      if (cameras) s.cachedCameras[nodeCfg.id] = cameras;
      if (!events) continue; // node unreachable: keep previous counts

      const lastSeen = s.lastSeenEventTs[nodeCfg.id];
      if (lastSeen === undefined) {
        // First poll for this node: baseline quietly, no badge/notifications.
        s.lastSeenEventTs[nodeCfg.id] = events[0]?.start_ts ?? Date.now() / 1000;
        s.unseenCounts[nodeCfg.id] = 0;
        continue;
      }

      const fresh = events.filter((e) => e.start_ts > lastSeen);
      s.unseenCounts[nodeCfg.id] = fresh.length;

      if (settings.notifyMode === "off") continue;
      for (const event of fresh) {
        if (!eventMatchesMode(event, settings.notifyMode)) continue;
        const key = `${nodeCfg.id}:${event.id}`;
        if (known.has(key)) continue;
        handledKeys.push(key);
        if (!quiet) candidates.push({ nodeCfg, event, key, cameras });
      }
    }
    // Mark every eligible event handled (including quiet-hours-suppressed
    // and over-cap ones) so old events never notify late.
    rememberNotified(s, handledKeys);
  });

  await updateBadge(settings, state);

  candidates.sort((a, b) => b.event.start_ts - a.event.start_ts);
  for (const candidate of candidates.slice(0, NOTIFY_CAP_PER_CYCLE)) {
    await notifyEvent(candidate);
  }
}

/**
 * Recompute and apply the badge text from per-node unseen counts.
 * @param {import("./lib/settings.js").Settings} settings
 * @param {import("./lib/settings.js").State} state
 */
async function updateBadge(settings, state) {
  if (!settings.badge) {
    await setBadgeText("");
    return;
  }
  const total = Object.values(state.unseenCounts).reduce((a, b) => a + b, 0);
  await setBadgeText(total > 0 ? (total > 99 ? "99+" : String(total)) : "");
}

/**
 * Fetch an event thumbnail and return it as a data: URL (MV3 notifications
 * cannot load remote icon URLs). Falls back to null on any failure.
 * @param {string} url
 * @returns {Promise<string|null>}
 */
async function thumbAsDataUrl(url) {
  try {
    const res = await fetch(url, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    const bytes = new Uint8Array(await res.arrayBuffer());
    let binary = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
    }
    return `data:image/jpeg;base64,${btoa(binary)}`;
  } catch {
    return null;
  }
}

/**
 * Show a notification for one new motion event.
 * @param {{nodeCfg: Object, event: Object, cameras: Array|null}} candidate
 */
async function notifyEvent({ nodeCfg, event, cameras }) {
  if (!ext.notifications) return;
  const api = createNode(nodeCfg);
  const camera = (cameras ?? []).find((c) => c.id === event.camera_id);
  const cameraName = camera?.name || event.camera_id;

  let iconUrl = null;
  if (event.has_thumb) {
    iconUrl = await thumbAsDataUrl(api.eventThumbUrl(event));
  }

  const label = event.label
    ? event.label.charAt(0).toUpperCase() + event.label.slice(1)
    : "Motion";
  try {
    await notificationsCreate(`${NOTIFICATION_PREFIX}${nodeCfg.id}|${event.id}`, {
      type: "basic",
      iconUrl: iconUrl ?? runtimeUrl("icons/icon-128.png"),
      title: `${labelChip(event.label)} ${label} — ${cameraName}`,
      message: event.description || `Motion detected on ${cameraName}`,
      contextMessage: `${nodeCfg.name || nodeCfg.url} · ${relativeTime(event.start_ts)}`,
    });
  } catch {
    // Notification permission missing or platform quirk: never break polling.
  }
}

/* ------------------------------------------------------------------ */
/* actions                                                             */
/* ------------------------------------------------------------------ */

/**
 * Open the glance popup window for a camera.
 * @param {string} nodeId
 * @param {string} cameraId
 */
function openGlance(nodeId, cameraId) {
  const url = runtimeUrl(
    `glance/glance.html?node=${encodeURIComponent(nodeId)}&camera=${encodeURIComponent(cameraId)}`,
  );
  return windowsCreate({ url, type: "popup", width: 480, height: 300 });
}

/**
 * Open the pinned camera's glance window, or the options page when no
 * camera is pinned yet.
 * @param {import("./lib/settings.js").Settings} settings
 */
function openPinnedGlance(settings) {
  if (settings.pinned?.nodeId && settings.pinned?.cameraId) {
    return openGlance(settings.pinned.nodeId, settings.pinned.cameraId);
  }
  return openOptionsPage();
}

/**
 * Open the first configured node's dashboard (or options when unconfigured).
 * @param {import("./lib/settings.js").Settings} settings
 * @param {string} [path] SPA route
 */
function openDashboard(settings, path = "/") {
  const node = settings.nodes[0];
  if (!node) return openOptionsPage();
  return tabsCreate({ url: createNode(node).dashboardUrl(path) });
}

/* ------------------------------------------------------------------ */
/* omnibox ("tc" keyword)                                              */
/* ------------------------------------------------------------------ */

/** Escape text for Chrome's XML-parsed omnibox descriptions. */
function xmlEscape(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

/**
 * Cached cameras across all nodes, each paired with its node config.
 * @returns {Promise<Array<{nodeCfg: Object, camera: Object}>>}
 */
async function allCachedCameras() {
  const [settings, state] = await Promise.all([loadSettings(), loadState()]);
  const out = [];
  for (const nodeCfg of settings.nodes) {
    for (const camera of state.cachedCameras[nodeCfg.id] ?? []) {
      out.push({ nodeCfg, camera });
    }
  }
  return out;
}

/**
 * Build omnibox suggestions for the typed text.
 * @param {string} text
 * @returns {Promise<Array<{content: string, description: string}>>}
 */
async function omniboxSuggestions(text) {
  const settings = await loadSettings();
  const firstNode = settings.nodes[0];
  const query = text.trim().toLowerCase();
  const suggestions = [];

  if (firstNode && (query === "" || "events".startsWith(query))) {
    suggestions.push({
      content: createNode(firstNode).dashboardUrl("/events"),
      description: `Events — ${xmlEscape(firstNode.name || firstNode.url)}`,
    });
  }
  for (const { nodeCfg, camera } of await allCachedCameras()) {
    if (query && !camera.name.toLowerCase().includes(query)) continue;
    suggestions.push({
      content: createNode(nodeCfg).cameraDashboardUrl(camera),
      description: `Camera: ${xmlEscape(camera.name)} — ${xmlEscape(nodeCfg.name || nodeCfg.url)}`,
    });
    if (suggestions.length >= 6) break;
  }
  return suggestions;
}

/**
 * Resolve the URL to open for an omnibox entry (suggestion content is
 * already a URL; free text is matched against cameras / "events").
 * @param {string} text
 * @returns {Promise<string|null>} URL, or null to open the options page
 */
async function omniboxTarget(text) {
  const entry = text.trim();
  if (/^https?:\/\//i.test(entry)) return entry;

  const settings = await loadSettings();
  const firstNode = settings.nodes[0];
  if (!firstNode) return null;

  const query = entry.toLowerCase();
  if (query === "events") return createNode(firstNode).dashboardUrl("/events");
  if (query) {
    const match = (await allCachedCameras()).find(({ camera }) =>
      camera.name.toLowerCase().includes(query),
    );
    if (match) {
      return createNode(match.nodeCfg).cameraDashboardUrl(match.camera);
    }
  }
  return createNode(firstNode).dashboardUrl("/");
}

/* ------------------------------------------------------------------ */
/* runtime message API (popup/options)                                 */
/* ------------------------------------------------------------------ */

/**
 * Handle a message from the popup or options page.
 * @param {{type: string, nodeId?: string, cameraId?: string, ts?: number}} msg
 * @returns {Promise<Object>} response payload
 */
async function handleMessage(msg) {
  switch (msg?.type) {
    case "markSeen": {
      const settings = await loadSettings();
      const nodeIds = msg.nodeId
        ? [msg.nodeId]
        : settings.nodes.map((n) => n.id);
      let state;
      for (const nodeId of nodeIds) {
        state = await markNodeSeen(nodeId, msg.ts);
      }
      if (state) await updateBadge(settings, state);
      return { ok: true };
    }
    case "getStatus": {
      const [settings, state] = await Promise.all([loadSettings(), loadState()]);
      return { ok: true, settings, state };
    }
    case "settingsChanged": {
      const settings = await loadSettings();
      await schedulePolling(settings);
      const state = await updateState((s) => pruneRemovedNodes(s, settings));
      await updateBadge(settings, state);
      pollAll(); // refresh with the new configuration; fire-and-forget
      return { ok: true };
    }
    case "openGlance": {
      if (msg.nodeId && msg.cameraId) {
        await openGlance(msg.nodeId, msg.cameraId);
      } else {
        await openPinnedGlance(await loadSettings());
      }
      return { ok: true };
    }
    default:
      return { ok: false, error: `unknown message type: ${msg?.type}` };
  }
}

/* ------------------------------------------------------------------ */
/* listener registration (must be synchronous at top level for MV3)    */
/* ------------------------------------------------------------------ */

ext.runtime.onInstalled.addListener(() => {
  init();
});

ext.runtime.onStartup.addListener(() => {
  init();
});

ext.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) pollAll();
});

ext.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  handleMessage(msg).then(sendResponse, (err) =>
    sendResponse({ ok: false, error: String(err?.message ?? err) }),
  );
  return true; // keep the channel open for the async response
});

if (ext.commands) {
  ext.commands.onCommand.addListener(async (command) => {
    const settings = await loadSettings();
    if (command === "open-dashboard") await openDashboard(settings);
    else if (command === "open-glance") await openPinnedGlance(settings);
  });
}

if (ext.contextMenus) {
  ext.contextMenus.onClicked.addListener(async (info) => {
    const settings = await loadSettings();
    if (info.menuItemId === MENU_DASHBOARD) await openDashboard(settings);
    else if (info.menuItemId === MENU_GLANCE) await openPinnedGlance(settings);
    else if (info.menuItemId === MENU_OPTIONS) await openOptionsPage();
  });
}

if (ext.notifications) {
  ext.notifications.onClicked.addListener(async (notificationId) => {
    if (!notificationId.startsWith(NOTIFICATION_PREFIX)) return;
    const nodeId = notificationId
      .slice(NOTIFICATION_PREFIX.length)
      .split("|")[0];
    const settings = await loadSettings();
    const nodeCfg =
      settings.nodes.find((n) => n.id === nodeId) ?? settings.nodes[0];
    if (nodeCfg) {
      await tabsCreate({ url: createNode(nodeCfg).dashboardUrl("/events") });
    }
    await notificationsClear(notificationId);
  });
}

if (ext.omnibox) {
  ext.omnibox.onInputChanged.addListener((text, suggest) => {
    omniboxSuggestions(text).then(suggest, () => suggest([]));
  });
  ext.omnibox.onInputEntered.addListener(async (text) => {
    const url = await omniboxTarget(text);
    if (url) await tabsCreate({ url });
    else await openOptionsPage();
  });
}
