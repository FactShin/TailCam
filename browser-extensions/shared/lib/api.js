/**
 * api.js — TailCam HTTP API client.
 *
 * URL rules (must match web-ui/src/api/client.ts exactly):
 *  - Camera ids may contain slashes ("/dev/video0") and are interpolated RAW
 *    (never encodeURIComponent) into /api and /stream URLs — the backend
 *    routes use {camera_id:path}.
 *  - Remote cameras/events carry `proxy_prefix` ("/proxy/<key>", "" = local)
 *    which must be prepended verbatim to EVERY path (streams, snapshots,
 *    thumbnails, POST actions).
 *  - SPA deep links are the opposite: /camera/<encodeURIComponent(host)>/
 *    <encodeURIComponent(id)> — both segments percent-encoded.
 *  - /stream, /events/<id>/thumbnail and /media routes are root-level,
 *    NOT under /api.
 */

const REQUEST_TIMEOUT_MS = 8000;

/**
 * Typed API error.
 * kind:
 *  - "network": fetch failed or timed out (node unreachable, no permission)
 *  - "blocked": 403 from the node's security middleware (extension Origin
 *    rejected on a mutating request) — surface "blocked by node security
 *    policy" in UIs
 *  - "http":    any other non-2xx response (note: 409 on recording start/stop
 *    is state info, not a real failure — check err.status)
 */
export class ApiError extends Error {
  /**
   * @param {"network"|"http"|"blocked"} kind
   * @param {string} message
   * @param {{status?: number, url?: string}} [info]
   */
  constructor(kind, message, { status = 0, url = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.url = url;
  }
}

/**
 * Cache-busting query fragment, same as the SPA's cacheBust().
 * @returns {string} e.g. "_=1720000000000"
 */
export const cacheBust = () => `_=${Date.now()}`;

/**
 * SPA deep-link path for a camera (percent-encoded segments).
 * @param {{host: string, id: string}} camera
 * @returns {string} e.g. "/camera/mybox/%2Fdev%2Fvideo0"
 */
export const cameraPagePath = (camera) =>
  `/camera/${encodeURIComponent(camera.host ?? "")}/${encodeURIComponent(camera.id)}`;

/**
 * A configured TailCam node (one base URL on the tailnet or localhost).
 * All methods honor each camera's/event's proxy_prefix so multi-host
 * (peer) setups work through a single node.
 */
export class TailCamNode {
  /**
   * @param {{id?: string, name?: string, url: string}} node
   */
  constructor({ id = "", name = "", url }) {
    this.id = id;
    this.name = name;
    /** Base URL without trailing slash, e.g. "https://host.tailnet.ts.net:8443" */
    this.url = String(url ?? "").replace(/\/+$/, "");
  }

  /** proxy_prefix of a camera/event/host object ("" for local). */
  #prefix(owner) {
    return (owner && owner.proxy_prefix) || "";
  }

  /**
   * Perform a JSON request against this node.
   * @param {string} path absolute path starting with "/"
   * @param {{method?: string}} [opts]
   * @returns {Promise<*>} parsed JSON body
   * @throws {ApiError}
   */
  async request(path, { method = "GET" } = {}) {
    const url = this.url + path;
    let res;
    try {
      res = await fetch(url, {
        method,
        cache: "no-store",
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch (err) {
      const timedOut = err && err.name === "TimeoutError";
      throw new ApiError(
        "network",
        timedOut ? "request timed out" : "node unreachable",
        { url },
      );
    }
    if (!res.ok) {
      let detail = "";
      try {
        detail = (await res.json())?.detail ?? "";
      } catch {
        /* non-JSON error body */
      }
      const blocked = res.status === 403 && /cross-origin/i.test(detail);
      throw new ApiError(
        blocked ? "blocked" : "http",
        detail || `HTTP ${res.status}`,
        { status: res.status, url },
      );
    }
    return res.json();
  }

  /* ---------------------------------------------------------------- */
  /* reads                                                             */
  /* ---------------------------------------------------------------- */

  /**
   * GET /api/system
   * @returns {Promise<{version: string, host: string, access_url: string, local_url: string}>}
   */
  system() {
    return this.request("/api/system");
  }

  /**
   * GET /api/cameras
   * @param {"all"|"local"} [scope]
   * @returns {Promise<Array<Object>>} CameraInfo list (id, name, status,
   *   recording, motion_enabled, last_error, host, proxy_prefix, ...)
   */
  cameras(scope = "all") {
    return this.request(`/api/cameras?scope=${scope}`);
  }

  /**
   * GET /api/hosts
   * @returns {Promise<Array<Object>>} HostInfo list
   */
  hosts() {
    return this.request("/api/hosts");
  }

  /**
   * GET /api/events — newest first, start_ts/end_ts are epoch seconds.
   * @param {{cameraId?: string, limit?: number, offset?: number, scope?: "all"|"local"}} [opts]
   * @returns {Promise<Array<Object>>} MotionEventInfo list
   */
  events({ cameraId, limit = 50, offset = 0, scope = "all" } = {}) {
    const params = [`limit=${limit}`, `offset=${offset}`, `scope=${scope}`];
    if (cameraId) params.push(`camera_id=${cameraId}`);
    return this.request(`/api/events?${params.join("&")}`);
  }

  /* ---------------------------------------------------------------- */
  /* mutations (may throw kind "blocked" if the node's security         */
  /* middleware rejects the extension Origin)                           */
  /* ---------------------------------------------------------------- */

  /**
   * POST <prefix>/api/cameras/<id>/snapshot
   * @param {{id: string, proxy_prefix?: string}} camera
   * @returns {Promise<{ok: boolean, media_id: number|null}>}
   */
  snapshot(camera) {
    return this.request(
      `${this.#prefix(camera)}/api/cameras/${camera.id}/snapshot`,
      { method: "POST" },
    );
  }

  /**
   * POST <prefix>/api/cameras/<id>/recording/start (409 = already recording).
   * @param {{id: string, proxy_prefix?: string}} camera
   * @returns {Promise<{ok: boolean, detail: string}>}
   */
  startRecording(camera) {
    return this.request(
      `${this.#prefix(camera)}/api/cameras/${camera.id}/recording/start`,
      { method: "POST" },
    );
  }

  /**
   * POST <prefix>/api/cameras/<id>/recording/stop (409 = not recording).
   * @param {{id: string, proxy_prefix?: string}} camera
   * @returns {Promise<{ok: boolean, media_id: number}>}
   */
  stopRecording(camera) {
    return this.request(
      `${this.#prefix(camera)}/api/cameras/${camera.id}/recording/stop`,
      { method: "POST" },
    );
  }

  /* ---------------------------------------------------------------- */
  /* URL builders (no request made)                                     */
  /* ---------------------------------------------------------------- */

  /**
   * MJPEG stream URL for an <img> element (never fetch() this).
   * @param {{id: string, proxy_prefix?: string}} camera
   * @param {{fps?: number, w?: number, q?: number}} [opts]
   * @returns {string}
   */
  streamUrl(camera, opts = {}) {
    const params = Object.entries(opts)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => `${k}=${v}`)
      .join("&");
    return (
      `${this.url}${this.#prefix(camera)}/stream/${camera.id}.mjpg` +
      (params ? `?${params}` : "")
    );
  }

  /**
   * One-shot JPEG snapshot URL (503 while the camera has no frame yet).
   * @param {{id: string, proxy_prefix?: string}} camera
   * @param {boolean} [bust] append a cache-busting query (default true)
   * @returns {string}
   */
  snapshotUrl(camera, bust = true) {
    const base = `${this.url}${this.#prefix(camera)}/stream/${camera.id}/snapshot.jpg`;
    return bust ? `${base}?${cacheBust()}` : base;
  }

  /**
   * Motion-event thumbnail URL (only valid when event.has_thumb).
   * @param {{id: number, proxy_prefix?: string}} event
   * @returns {string}
   */
  eventThumbUrl(event) {
    return `${this.url}${this.#prefix(event)}/events/${event.id}/thumbnail`;
  }

  /**
   * URL into this node's dashboard SPA.
   * @param {string} [path] SPA route, e.g. "/", "/events", "/settings"
   * @returns {string}
   */
  dashboardUrl(path = "/") {
    return this.url + (path.startsWith("/") ? path : `/${path}`);
  }

  /**
   * Deep link to a camera's detail page in the dashboard SPA.
   * @param {{host: string, id: string}} camera
   * @returns {string}
   */
  cameraDashboardUrl(camera) {
    return this.url + cameraPagePath(camera);
  }
}

/**
 * Factory for a TailCamNode from a stored settings node.
 * @param {{id?: string, name?: string, url: string}} node
 * @returns {TailCamNode}
 */
export const createNode = (node) => new TailCamNode(node);
