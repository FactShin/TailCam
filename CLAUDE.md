# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TailCam turns any Linux/macOS/Windows machine with webcams into a private camera system
reachable over Tailscale. One Python package (`src/tailcam/`) contains the capture stack,
FastAPI server, MCP server, plugin system, and desktop app; the dashboard is a separate
React/Vite app (`web-ui/`) whose **built output is committed** into the package.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

TAILCAM_SYNTHETIC=1 tailcam run     # run the server with a fake camera (no hardware)
pytest                              # whole suite; conftest forces the synthetic source
pytest tests/test_mcp.py -q         # one file
pytest tests/test_mcp.py::test_stdio_principal_is_local_admin -q   # one test
ruff check .                        # line-length 100; rules E,F,I,UP,B,W
mypy src
```

Dashboard (only needed when changing `web-ui/`):

```bash
cd web-ui && npm install
npm run dev            # Vite dev server; proxies /api,/stream,/media,/proxy to
                       # $TAILCAM_DEV_TARGET (default http://localhost:8088)
npm run typecheck
npm run build          # writes src/tailcam/web/spa/ — COMMIT that output too
```

CI (`.github/workflows/`) only covers the desktop app (Linux/macOS/Windows GUI smoke
tests) and the Docker image publish. **The Python suite, ruff, and mypy are not run by
CI — run them locally before pushing.**

## Version invariant

Three files must carry the identical version string, and tests enforce it:
`src/tailcam/__init__.py` (`__version__`, the source of truth), `web-ui/package.json`,
and every `browser-extensions/*/manifest.json`. Bump all of them together on any change
merged to `main` (`tests/test_version.py`, `tests/test_browser_extensions.py`).

## Architecture

### Capture pipeline (the core seam)

`CameraWorker` (one thread per camera, `camera/worker.py`) owns a `CameraSource` and
publishes into a `FrameBuffer` (`camera/frame.py`). The buffer keeps **only the latest
frame** — a slow consumer drops frames and can never back-pressure capture. Consumers
(MJPEG streams, `media/recorder.py`, `motion/worker.py`) all read from it.

Two rules this design depends on:
- `camera/source.py` is the *only* module that touches `cv2.VideoCapture`. `TAILCAM_SYNTHETIC=1`
  swaps in `SyntheticCameraSource` so the whole stack runs headless.
- Device mutations (properties, transforms, reconnect) are queued as commands onto the
  worker thread — never call into the capture object from a request thread.

Output goes through the `StreamBackend` contract (`streaming/backend.py`); `MJPEGBackend`
is the only implementation today, and JPEG encoding is offloaded to a thread pool so the
event loop never blocks.

### Composition root

`web/context.py::AppContext` wires every service (camera manager, store, motion, media,
notifications, AI/detector, training, active learning, timelapse, cluster, plugins,
integrations) and owns startup/shutdown. `web/app.py::create_app` is the FastAPI factory.
New services get constructed in `AppContext`, not inside routes.

### State split

- **`config.toml`** — user-editable settings as nested dataclasses in `config.py`
  (`AppConfig.load()`), saved via an atomic temp-file + `os.replace` (mode `0600`).
- **SQLite** — `persistence/store.py`, WAL mode. Holds the camera registry, media index,
  motion events, audit log, datasets/models/training runs. `Store.migrate()` runs
  `CREATE TABLE IF NOT EXISTS` for every table, then adds later columns via explicit
  `PRAGMA table_info` checks + `ALTER TABLE` — follow that pattern when adding a column
  to an existing table, and bump `_CURRENT_VERSION`.
- **`paths.py`** resolves per-OS config/data dirs and honors `TAILCAM_CONFIG_DIR`,
  `TAILCAM_CONFIG`, `TAILCAM_DATA_DIR` (used by the service installers and every test).
  `set_media_override()` redirects recordings to a custom drive without moving the DB.

### HTTP surface (route order matters)

| Prefix | Module | Purpose |
| --- | --- | --- |
| `/api` | `routes_api.py` | main REST surface for the dashboard |
| `/api/v1/node` | `routes_node_v1.py` | node management — role-gated + audited |
| `/api/v1/fleet` | `routes_fleet_v1.py` | explicit allowlisted relay of the above to peers |
| `/stream`, `/media` | `routes_stream.py` | MJPEG, snapshots, media files |
| `/proxy/{peer}/...` | `routes_proxy.py` | generic reverse proxy to a peer node |
| `/api/active-learning` | `routes_active.py` | Label Studio / fine-tune workflows |
| `/mcp` | `mcp/transport_http.py` | Streamable-HTTP MCP endpoint |
| `/{full_path}` | `web/app.py` | SPA catch-all — registered **last** |

The SPA catch-all excludes `_API_PREFIXES`; any new top-level prefix must be added there
or client-side routing will swallow it. FastAPI's own docs live at `/api-docs` because
`/docs` is the in-app wiki. `routes_pages.py` (Jinja) is a fallback used *only* when
`src/tailcam/web/spa/` is missing.

### Fleet

`cluster/service.py` makes each node an aggregator: it discovers peers (Tailscale status +
static config), fetches their camera lists, and proxies remote streams so the browser only
ever talks to the node it opened. Peers are always queried with `?scope=local` — that is
what prevents recursive fan-out. The proxy deliberately does not follow redirects (SSRF).

### Security model

The tailnet is the boundary: bind to `127.0.0.1`, no per-request login. Layered on top:

- `web/security.py::SecurityMiddleware` — security headers on every response, plus an
  Origin/Host guard that rejects mutating requests from foreign origins or rebound
  hostnames (only loopback, IP literals, and `*.ts.net` may mutate).
- `security/principal.py::principal_from_request` — derives a `RequestPrincipal` and its
  roles (`viewer`/`operator`/`admin`) from Tailscale identity headers, and only when the
  connection is loopback. Subtlety worth preserving: a verified user gets admin *only*
  when no `tailcam` app-capability grant exists at all ("personal mode"); if any grant is
  present, the grant's roles win. Treating a verified user as admin unconditionally was a
  past privilege-escalation bug.
- MCP tools declare `min_role` and a `write` flag; the server filters `tools/list` by role
  and audits every write through `management/audit.py`.

### MCP

`mcp/server.py` is a transport-agnostic core (one `McpServer` per stdio process or HTTP
request) handling handshake, registries, authorization, and result shaping. Transports
(`transport_stdio.py`, `transport_http.py`) only supply parsed JSON-RPC plus a resolved
principal. Tools live in `mcp/tools.py` and return a `ToolResult` (human `summary` +
machine `data`); failures raise `TailcamMcpError`. The HTTP mount is always registered but
fail-closed — it re-checks `[mcp] enabled/http_enabled` per request so the UI toggle takes
effect without a restart.

### Plugins

pluggy-based (`plugins/hookspecs.py`). Sources: built-ins, `tailcam` entry-point group, and
drop-in `*.py` files in `<config-dir>/plugins/`. `plugins/sdk.py` is the single public
import surface for authors — keep it stable. `plugins/market.py` installs from
`marketplace/index.json` with sha256 pinning, size check, syntax check, atomic write;
plugins run unsandboxed, so curation + verification + explicit user action are the defenses.

## Conventions and traps

- **Optional extras are optional.** `torch`, `ultralytics`, `HAP-python`, `paho-mqtt`,
  `aiortc`, `pywebview`/`pystray`, `pygrabber` are extras — guard imports and degrade with a
  clear message rather than failing at import time.
- **Windows subprocesses go through `tailcam/proc.py`.** The service runs under
  `pythonw.exe`; any child started without `CREATE_NO_WINDOW` pops a visible console.
- **Plugin/notification/hook failures must never break capture or detection** — the
  motion fanout in `web/context.py` catches per-hook exceptions on purpose.
- **Python 3.10 floor** — `tomllib` is conditional on 3.11+ (`tomli` below).
- Test fixtures (`tests/conftest.py`): `isolated_env`, `store`, `context`, `client`.
  `TestClient` must use `base_url="http://localhost:8088"` — the default `testserver` host
  is rejected by the anti-DNS-rebinding guard. Detector model downloads are stubbed
  globally; don't undo that.
- Some tests are repo guardrails rather than unit tests: `test_installer_script.py` pins
  textual invariants of `install.ps1`, `test_browser_extensions.py` validates the shared
  extension manifests.
- In-app docs are markdown in `web-ui/src/docs/md/` registered in `web-ui/src/docs/index.ts`;
  a new page needs both, plus an `npm run build` to reach users.
