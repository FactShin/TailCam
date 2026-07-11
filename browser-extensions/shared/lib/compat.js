/**
 * compat.js — single choke point for WebExtension API access.
 *
 * Every other module imports `ext` (and the helpers below) from here and
 * never touches `chrome.*` / `browser.*` directly. All supported targets
 * (Chrome/Edge MV3, Firefox MV3, Safari web extensions) return Promises
 * from these namespaces when no callback is passed; the helpers normalize
 * the return value with Promise.resolve() so callers can always `await`.
 */

/** The WebExtension API root (Firefox/Safari `browser`, Chrome/Edge `chrome`). */
export const ext = globalThis.browser ?? globalThis.chrome;

/**
 * Normalize a possibly-undefined / possibly-thenable return value.
 * @param {*} value
 * @returns {Promise<*>}
 */
const asPromise = (value) => Promise.resolve(value);

/* ------------------------------------------------------------------ */
/* storage                                                             */
/* ------------------------------------------------------------------ */

/**
 * Read keys from ext.storage.local.
 * @param {string|string[]|Object|null} keys
 * @returns {Promise<Object>} map of key -> stored value
 */
export const storageGet = (keys) => asPromise(ext.storage.local.get(keys));

/**
 * Write items to ext.storage.local.
 * @param {Object} items
 * @returns {Promise<void>}
 */
export const storageSet = (items) => asPromise(ext.storage.local.set(items));

/* ------------------------------------------------------------------ */
/* permissions                                                         */
/* ------------------------------------------------------------------ */

/**
 * Request optional permissions (must be called from a user gesture).
 * @param {{origins?: string[], permissions?: string[]}} perms
 * @returns {Promise<boolean>} true when granted
 */
export const permissionsRequest = (perms) =>
  asPromise(ext.permissions.request(perms));

/**
 * Check whether permissions are currently granted.
 * @param {{origins?: string[], permissions?: string[]}} perms
 * @returns {Promise<boolean>}
 */
export const permissionsContains = (perms) =>
  asPromise(ext.permissions.contains(perms));

/* ------------------------------------------------------------------ */
/* notifications                                                       */
/* ------------------------------------------------------------------ */

/**
 * Create (or replace) a notification.
 * @param {string} id notification id
 * @param {Object} options chrome.notifications options ({type,title,message,iconUrl,...})
 * @returns {Promise<string>} the notification id
 */
export const notificationsCreate = (id, options) =>
  asPromise(ext.notifications.create(id, options));

/**
 * Clear a notification by id.
 * @param {string} id
 * @returns {Promise<boolean>}
 */
export const notificationsClear = (id) =>
  asPromise(ext.notifications.clear(id));

/* ------------------------------------------------------------------ */
/* windows / tabs                                                      */
/* ------------------------------------------------------------------ */

/**
 * Create a browser window.
 * @param {Object} createData e.g. {url, type: "popup", width, height}
 * @returns {Promise<Object>} the created window
 */
export const windowsCreate = (createData) =>
  asPromise(ext.windows.create(createData));

/**
 * Open a new tab.
 * @param {Object} createProperties e.g. {url}
 * @returns {Promise<Object>} the created tab
 */
export const tabsCreate = (createProperties) =>
  asPromise(ext.tabs.create(createProperties));

/**
 * Query tabs.
 * @param {Object} queryInfo
 * @returns {Promise<Object[]>}
 */
export const tabsQuery = (queryInfo) => asPromise(ext.tabs.query(queryInfo));

/* ------------------------------------------------------------------ */
/* alarms                                                              */
/* ------------------------------------------------------------------ */

/**
 * Create (or replace, by name) an alarm.
 * @param {string} name
 * @param {{periodInMinutes?: number, delayInMinutes?: number, when?: number}} alarmInfo
 * @returns {Promise<void>}
 */
export const alarmsCreate = (name, alarmInfo) =>
  asPromise(ext.alarms.create(name, alarmInfo));

/**
 * Clear an alarm by name.
 * @param {string} name
 * @returns {Promise<boolean>}
 */
export const alarmsClear = (name) => asPromise(ext.alarms.clear(name));

/**
 * Get an alarm by name (undefined when not scheduled).
 * @param {string} name
 * @returns {Promise<Object|undefined>}
 */
export const alarmsGet = (name) => asPromise(ext.alarms.get(name));

/* ------------------------------------------------------------------ */
/* action (toolbar badge)                                              */
/* ------------------------------------------------------------------ */

/**
 * Set the toolbar badge text ("" hides the badge).
 * @param {string} text
 * @returns {Promise<void>}
 */
export const setBadgeText = (text) =>
  asPromise(ext.action.setBadgeText({ text }));

/**
 * Set the toolbar badge background color.
 * @param {string} color CSS color string
 * @returns {Promise<void>}
 */
export const setBadgeBackgroundColor = (color) =>
  asPromise(ext.action.setBadgeBackgroundColor({ color }));

/* ------------------------------------------------------------------ */
/* misc                                                                */
/* ------------------------------------------------------------------ */

/**
 * Open the extension's options page.
 * @returns {Promise<void>}
 */
export const openOptionsPage = () => asPromise(ext.runtime.openOptionsPage());

/**
 * Send a runtime message to the background and await the response.
 * @param {Object} message
 * @returns {Promise<*>}
 */
export const sendMessage = (message) =>
  asPromise(ext.runtime.sendMessage(message));

/**
 * Absolute extension URL for a packaged resource path (query strings allowed).
 * @param {string} path e.g. "glance/glance.html?node=x&camera=y"
 * @returns {string}
 */
export const runtimeUrl = (path) => ext.runtime.getURL(path);
