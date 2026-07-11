/**
 * options.js — TailCam Companion options page.
 *
 * Manages nodes (with runtime host-permission requests + connection tests),
 * polling/badge settings, notification mode + quiet hours, and the pinned
 * camera. Every save notifies the background ({type:"settingsChanged"}) so
 * it reschedules its polling alarm.
 */

import { ext, permissionsRequest, permissionsContains, sendMessage } from "../lib/compat.js";
import { createNode } from "../lib/api.js";
import { loadSettings, saveSettings, loadState } from "../lib/settings.js";

/** Live settings object; mutated in place then persisted via persist(). */
let settings = null;

/** Pinned-camera dropdown choices; option value = index into this array. */
let pinnedChoices = [];

/* ------------------------------------------------------------------ */
/* tiny DOM helpers (no innerHTML with dynamic data)                    */
/* ------------------------------------------------------------------ */

/** @param {string} id @returns {HTMLElement} */
const $ = (id) => document.getElementById(id);

/**
 * Create an element with class, text, and children.
 * @param {string} tag
 * @param {{className?: string, text?: string, title?: string}} [props]
 * @param {HTMLElement[]} [children]
 * @returns {HTMLElement}
 */
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  if (props.className) node.className = props.className;
  if (props.text !== undefined) node.textContent = props.text;
  if (props.title) node.title = props.title;
  for (const child of children) node.appendChild(child);
  return node;
}

/** Show the "Saved" toast briefly. */
let toastTimer = 0;
function showToast(text = "Saved") {
  const toast = $("toast");
  toast.textContent = text;
  toast.hidden = false;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 1400);
}

/* ------------------------------------------------------------------ */
/* persistence                                                          */
/* ------------------------------------------------------------------ */

/**
 * Save settings, notify the background so it reschedules alarms, and flash
 * the toast.
 * @returns {Promise<void>}
 */
async function persist() {
  await saveSettings(settings);
  try {
    await sendMessage({ type: "settingsChanged" });
  } catch {
    /* background not awake yet — it re-reads storage on its next event */
  }
  showToast();
}

/* ------------------------------------------------------------------ */
/* URL / permission helpers                                             */
/* ------------------------------------------------------------------ */

/**
 * Normalize a user-entered node URL: require http(s), strip trailing
 * slashes, drop query/hash.
 * @param {string} raw
 * @returns {{url: string}|{error: string}}
 */
function normalizeUrl(raw) {
  const trimmed = String(raw ?? "").trim();
  if (!/^https?:\/\//i.test(trimmed)) {
    return { error: "The URL must start with http:// or https://." };
  }
  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    return { error: "That doesn't look like a valid URL." };
  }
  const path = parsed.pathname.replace(/\/+$/, "");
  return { url: parsed.origin + path };
}

/**
 * Host-permission match pattern for a node URL's origin.
 * @param {string} url normalized node URL
 * @returns {string} e.g. "https://host.tailnet.ts.net:8443/*"
 */
const originPattern = (url) => new URL(url).origin + "/*";

/**
 * Request the host permission for a node (no prompt when already granted).
 * Must be called from a user gesture.
 * @param {string} url normalized node URL
 * @returns {Promise<boolean>} true when granted
 */
async function requestNodePermission(url) {
  try {
    return await permissionsRequest({ origins: [originPattern(url)] });
  } catch {
    return false;
  }
}

/**
 * Release a node's host permission unless another configured node shares
 * the same origin. Errors are swallowed (permission may not exist).
 * @param {string} url normalized node URL of the removed node
 * @returns {Promise<void>}
 */
async function releaseNodePermission(url) {
  const origin = new URL(url).origin;
  const stillUsed = settings.nodes.some((n) => {
    try {
      return new URL(n.url).origin === origin;
    } catch {
      return false;
    }
  });
  if (stillUsed) return;
  try {
    await Promise.resolve(ext.permissions.remove({ origins: [origin + "/*"] }));
  } catch {
    /* nothing to release */
  }
}

/* ------------------------------------------------------------------ */
/* nodes section                                                        */
/* ------------------------------------------------------------------ */

/**
 * Set a node row's status line.
 * @param {HTMLElement} statusEl
 * @param {"ok"|"err"|"warn"|"busy"|""} kind
 * @param {string} text
 */
function setNodeStatus(statusEl, kind, text) {
  statusEl.className = `node-status${kind ? ` ${kind}` : ""}`;
  statusEl.replaceChildren();
  if (!text) return;
  statusEl.appendChild(el("span", { className: "led" }));
  statusEl.appendChild(el("span", { text }));
}

/**
 * Build one node row (name, url, Test/Remove buttons, status line).
 * @param {import("../lib/settings.js").NodeConfig} node
 * @returns {HTMLElement}
 */
function renderNodeRow(node) {
  const statusEl = el("p", { className: "node-status" });

  const testBtn = el("button", { className: "btn small", text: "Test connection" });
  testBtn.type = "button";
  testBtn.addEventListener("click", () => testNode(node, testBtn, statusEl));

  const removeBtn = el("button", { className: "btn small danger", text: "Remove" });
  removeBtn.type = "button";
  removeBtn.addEventListener("click", () => removeNode(node));

  const row = el("li", { className: "node-item" }, [
    el("div", { className: "node-head" }, [
      el("span", { className: "node-name", text: node.name, title: node.name }),
      el("span", { className: "node-url", text: node.url, title: node.url }),
      el("div", { className: "node-actions" }, [testBtn, removeBtn]),
    ]),
    statusEl,
  ]);

  // Warn (async) when the host permission for this node is missing.
  permissionsContains({ origins: [originPattern(node.url)] })
    .then((granted) => {
      if (granted || !statusEl.isConnected) return;
      if (statusEl.classList.contains("node-status") && !statusEl.textContent) {
        setNodeStatus(
          statusEl,
          "warn",
          "Permission not granted — use “Test connection” to grant access.",
        );
      }
    })
    .catch(() => {});

  return row;
}

/** Re-render the node list and the empty-state message. */
function renderNodes() {
  const list = $("node-list");
  list.replaceChildren(...settings.nodes.map(renderNodeRow));
  $("nodes-empty").hidden = settings.nodes.length > 0;
}

/**
 * "Test connection": (re)request the host permission, then GET /api/system
 * and report "TailCam <version> on <host>" or a specific error.
 * @param {import("../lib/settings.js").NodeConfig} node
 * @param {HTMLButtonElement} button
 * @param {HTMLElement} statusEl
 */
async function testNode(node, button, statusEl) {
  button.disabled = true;
  const granted = await requestNodePermission(node.url);
  if (!granted) {
    setNodeStatus(
      statusEl,
      "err",
      "Permission denied — the extension can't reach this node until you grant access.",
    );
    button.disabled = false;
    return;
  }
  setNodeStatus(statusEl, "busy", "Testing…");
  try {
    const info = await createNode(node).system();
    setNodeStatus(statusEl, "ok", `TailCam ${info.version} on ${info.host}`);
  } catch (err) {
    const text =
      err?.kind === "network"
        ? "Node unreachable — check the URL and that the node is online."
        : `Unexpected response from the node (${err?.message ?? "unknown error"}).`;
    setNodeStatus(statusEl, "err", text);
  } finally {
    button.disabled = false;
  }
}

/**
 * Remove a node: drop it from settings, clear a pinned camera pointing at
 * it, release its host permission, persist, re-render.
 * @param {import("../lib/settings.js").NodeConfig} node
 */
async function removeNode(node) {
  settings.nodes = settings.nodes.filter((n) => n.id !== node.id);
  if (settings.pinned?.nodeId === node.id) settings.pinned = null;
  await releaseNodePermission(node.url);
  await persist();
  renderNodes();
  await renderPinnedSelect();
}

/**
 * Handle the add-node form: validate, request the host permission (within
 * the submit gesture), save, and surface a clear warning when denied.
 * @param {SubmitEvent} event
 */
async function addNode(event) {
  event.preventDefault();
  const errorEl = $("add-node-error");
  errorEl.hidden = true;

  const name = $("node-name").value.trim();
  const result = normalizeUrl($("node-url").value);
  if (!name) {
    errorEl.textContent = "Give the node a name.";
    errorEl.hidden = false;
    return;
  }
  if (result.error) {
    errorEl.textContent = result.error;
    errorEl.hidden = false;
    return;
  }
  if (settings.nodes.some((n) => n.url === result.url)) {
    errorEl.textContent = "That node URL is already configured.";
    errorEl.hidden = false;
    return;
  }

  // Request the permission first, while still inside the user gesture.
  const granted = await requestNodePermission(result.url);

  settings.nodes.push({ id: crypto.randomUUID(), name, url: result.url });
  await persist();

  $("node-name").value = "";
  $("node-url").value = "";
  renderNodes();
  await renderPinnedSelect();

  if (!granted) {
    errorEl.textContent =
      "Node saved, but the host permission was denied — the extension cannot " +
      "reach it until you grant access via “Test connection”.";
    errorEl.hidden = false;
  }
}

/* ------------------------------------------------------------------ */
/* polling / badge / notifications                                      */
/* ------------------------------------------------------------------ */

/** Populate the polling + badge + notification controls from settings. */
function renderPrefs() {
  const pollSelect = $("poll-interval");
  const value = String(settings.pollSeconds);
  if (![...pollSelect.options].some((o) => o.value === value)) {
    const custom = new Option(`${settings.pollSeconds} seconds`, value);
    pollSelect.appendChild(custom);
  }
  pollSelect.value = value;

  $("badge-enabled").checked = settings.badge;

  const radio = document.querySelector(
    `input[name="notify-mode"][value="${settings.notifyMode}"]`,
  );
  if (radio) radio.checked = true;

  $("quiet-start").value = settings.quietStart;
  $("quiet-end").value = settings.quietEnd;
}

/** Wire change handlers that autosave preference controls. */
function bindPrefs() {
  $("poll-interval").addEventListener("change", async (e) => {
    settings.pollSeconds = Number(e.target.value) || 60;
    await persist();
  });
  $("badge-enabled").addEventListener("change", async (e) => {
    settings.badge = e.target.checked;
    await persist();
  });
  $("notify-mode-group").addEventListener("change", async (e) => {
    if (e.target.name === "notify-mode") {
      settings.notifyMode = e.target.value;
      await persist();
    }
  });
  $("quiet-start").addEventListener("change", async (e) => {
    settings.quietStart = e.target.value || "00:00";
    await persist();
  });
  $("quiet-end").addEventListener("change", async (e) => {
    settings.quietEnd = e.target.value || "00:00";
    await persist();
  });
}

/* ------------------------------------------------------------------ */
/* pinned camera                                                        */
/* ------------------------------------------------------------------ */

/**
 * Rebuild the pinned-camera dropdown from the cached camera lists in state
 * (populated by the background's poll cycle).
 * @returns {Promise<void>}
 */
async function renderPinnedSelect() {
  const select = $("pinned-camera");
  const state = await loadState();

  pinnedChoices = [];
  const options = [new Option("None", "")];

  for (const node of settings.nodes) {
    for (const cam of state.cachedCameras[node.id] ?? []) {
      const remote = cam.proxy_prefix ? ` @ ${cam.host}` : "";
      const label = `${node.name} · ${cam.name || cam.id}${remote}`;
      options.push(new Option(label, String(pinnedChoices.length)));
      pinnedChoices.push({ nodeId: node.id, cameraId: cam.id });
    }
  }

  // Keep a pinned camera visible even if it is not in the cache (yet).
  const pinned = settings.pinned;
  let selectedValue = "";
  if (pinned?.nodeId && pinned?.cameraId) {
    const idx = pinnedChoices.findIndex(
      (c) => c.nodeId === pinned.nodeId && c.cameraId === pinned.cameraId,
    );
    if (idx >= 0) {
      selectedValue = String(idx);
    } else {
      options.push(
        new Option(`${pinned.cameraId} (not in cache)`, String(pinnedChoices.length)),
      );
      pinnedChoices.push({ nodeId: pinned.nodeId, cameraId: pinned.cameraId });
      selectedValue = String(pinnedChoices.length - 1);
    }
  }

  select.replaceChildren(...options);
  select.value = selectedValue;
  select.disabled = options.length === 1;
}

/** Wire the pinned-camera dropdown. */
function bindPinnedSelect() {
  $("pinned-camera").addEventListener("change", async (e) => {
    const choice = pinnedChoices[Number(e.target.value)];
    settings.pinned = choice ? { nodeId: choice.nodeId, cameraId: choice.cameraId } : null;
    await persist();
  });
}

/* ------------------------------------------------------------------ */
/* init                                                                 */
/* ------------------------------------------------------------------ */

/** Load settings + state and render the whole page. */
async function init() {
  settings = await loadSettings();

  $("add-node-form").addEventListener("submit", addNode);
  bindPrefs();
  bindPinnedSelect();

  renderNodes();
  renderPrefs();
  await renderPinnedSelect();
}

init();
