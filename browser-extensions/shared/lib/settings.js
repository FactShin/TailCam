/**
 * settings.js — persistent settings + transient state in ext.storage.local.
 *
 * Two keys:
 *  - "settings": per-install user configuration (nodes, polling, notifications).
 *  - "state":    transient data that must survive service-worker shutdown
 *                (last-seen event timestamps, cached camera lists, dedupe keys).
 *
 * All loads are migration-safe: stored values are merged over defaults key by
 * key with a type check, so adding/removing fields across versions never
 * yields undefined or wrongly-typed settings.
 */

import { ext, storageGet, storageSet } from "./compat.js";

const SETTINGS_KEY = "settings";
const STATE_KEY = "state";

/** Maximum notification-dedupe keys kept in state. */
const MAX_NOTIFIED_KEYS = 400;

/**
 * @typedef {Object} NodeConfig
 * @property {string} id   stable unique id (e.g. crypto.randomUUID())
 * @property {string} name display name
 * @property {string} url  base URL, e.g. "https://host.tailnet.ts.net:8443"
 */

/**
 * @typedef {Object} Settings
 * @property {NodeConfig[]} nodes
 * @property {number} pollSeconds   badge/notification poll interval
 * @property {boolean} badge        show unseen-event badge
 * @property {"off"|"all"|"labeled"|"person"} notifyMode
 * @property {string} quietStart    "HH:MM" local time
 * @property {string} quietEnd      "HH:MM" local time
 * @property {{nodeId: string, cameraId: string}|null} pinned
 */

/** @type {Settings} */
export const DEFAULT_SETTINGS = Object.freeze({
  nodes: [],
  pollSeconds: 60,
  badge: true,
  notifyMode: "off",
  quietStart: "22:00",
  quietEnd: "07:00",
  pinned: null,
});

/**
 * @typedef {Object} State
 * @property {Object<string, number>} lastSeenEventTs  nodeId -> epoch seconds
 * @property {Object<string, Array>}  cachedCameras    nodeId -> CameraInfo[]
 * @property {Object<string, number>} unseenCounts     nodeId -> unseen event count
 * @property {string[]} notifiedEventKeys              "nodeId:eventId" dedupe keys
 */

/** @type {State} */
export const DEFAULT_STATE = Object.freeze({
  lastSeenEventTs: {},
  cachedCameras: {},
  unseenCounts: {},
  notifiedEventKeys: [],
});

/**
 * Merge stored values over defaults, keeping a stored value only when its
 * type matches the default's (arrays must stay arrays, etc.). Nullable
 * object fields (default null) accept null or plain objects.
 * @param {Object} defaults
 * @param {Object|undefined} stored
 * @returns {Object} fresh merged object
 */
function mergeDefaults(defaults, stored) {
  const out = {};
  for (const [key, def] of Object.entries(defaults)) {
    const val = stored?.[key];
    if (val === undefined) {
      out[key] = structuredClone(def);
    } else if (def === null) {
      out[key] = val === null || typeof val === "object" ? val : null;
    } else if (Array.isArray(def)) {
      out[key] = Array.isArray(val) ? val : structuredClone(def);
    } else if (typeof val === typeof def) {
      out[key] = val;
    } else {
      out[key] = structuredClone(def);
    }
  }
  return out;
}

/**
 * Load settings (defaults merged in).
 * @returns {Promise<Settings>}
 */
export async function loadSettings() {
  const stored = await storageGet(SETTINGS_KEY);
  return mergeDefaults(DEFAULT_SETTINGS, stored[SETTINGS_KEY]);
}

/**
 * Persist settings.
 * @param {Settings} settings
 * @returns {Promise<void>}
 */
export function saveSettings(settings) {
  return storageSet({ [SETTINGS_KEY]: settings });
}

/**
 * Subscribe to settings changes (any context). The callback receives the
 * new merged Settings object.
 * @param {(settings: Settings) => void} callback
 * @returns {() => void} unsubscribe function
 */
export function onSettingsChanged(callback) {
  const listener = (changes, area) => {
    if (area === "local" && changes[SETTINGS_KEY]) {
      callback(mergeDefaults(DEFAULT_SETTINGS, changes[SETTINGS_KEY].newValue));
    }
  };
  ext.storage.onChanged.addListener(listener);
  return () => ext.storage.onChanged.removeListener(listener);
}

/**
 * Load transient state (defaults merged in).
 * @returns {Promise<State>}
 */
export async function loadState() {
  const stored = await storageGet(STATE_KEY);
  return mergeDefaults(DEFAULT_STATE, stored[STATE_KEY]);
}

/**
 * Persist transient state.
 * @param {State} state
 * @returns {Promise<void>}
 */
export function saveState(state) {
  return storageSet({ [STATE_KEY]: state });
}

/**
 * Read-modify-write helper for state. The mutator receives the current
 * state and may modify it in place or return a replacement.
 * @param {(state: State) => (State|void)} mutator
 * @returns {Promise<State>} the saved state
 */
export async function updateState(mutator) {
  const state = await loadState();
  const next = mutator(state) ?? state;
  await saveState(next);
  return next;
}

/**
 * Mark a node's events as seen up to a timestamp and clear its unseen count.
 * @param {string} nodeId
 * @param {number} [tsSeconds] defaults to now
 * @returns {Promise<State>}
 */
export function markNodeSeen(nodeId, tsSeconds = Date.now() / 1000) {
  return updateState((state) => {
    state.lastSeenEventTs[nodeId] = Math.max(
      state.lastSeenEventTs[nodeId] ?? 0,
      tsSeconds,
    );
    state.unseenCounts[nodeId] = 0;
  });
}

/**
 * Store the cached camera list for a node (used by popup, omnibox, options).
 * @param {string} nodeId
 * @param {Array} cameras CameraInfo[]
 * @returns {Promise<State>}
 */
export function setCachedCameras(nodeId, cameras) {
  return updateState((state) => {
    state.cachedCameras[nodeId] = cameras;
  });
}

/**
 * Record notification dedupe keys, pruning the list to a bounded length.
 * @param {State} state mutated in place (use inside updateState)
 * @param {string[]} keys "nodeId:eventId" keys just notified
 */
export function rememberNotified(state, keys) {
  state.notifiedEventKeys = [...state.notifiedEventKeys, ...keys].slice(
    -MAX_NOTIFIED_KEYS,
  );
}

/**
 * Drop state entries belonging to nodes that no longer exist.
 * @param {State} state mutated in place (use inside updateState)
 * @param {Settings} settings
 */
export function pruneRemovedNodes(state, settings) {
  const ids = new Set(settings.nodes.map((n) => n.id));
  for (const map of [state.lastSeenEventTs, state.cachedCameras, state.unseenCounts]) {
    for (const key of Object.keys(map)) {
      if (!ids.has(key)) delete map[key];
    }
  }
}
