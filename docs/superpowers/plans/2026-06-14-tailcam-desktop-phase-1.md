# TailCam macOS Desktop Alpha And Fleet Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first useful TailCam macOS desktop application with a native menu-bar Command Center, bundled/attachable local node lifecycle, explicit fleet-health APIs, Tailscale identity foundations, and a shared Windows-ready desktop core.

**Architecture:** Extend the current Python/FastAPI node with explicit versioned node-health and authorization contracts, while preserving existing `/api`, streaming, PWA, and proxy behavior. Add a Tauri 2.11.2 shell that loads the local TailCam origin, attaches to an installed node when available, otherwise launches a PyInstaller 6.20.0 sidecar, and exposes native menu-bar/window/notification behavior through small cross-platform Rust modules.

**Tech Stack:** Python 3.10+, FastAPI/Pydantic/SQLite, React 18/TypeScript/Vite/React Query, Tauri 2.11.2/Rust, PyInstaller 6.20.0, Tailscale Serve identity headers and `Tailscale-App-Capabilities`.

**Implementation baseline:** Start from `FactShin/TailCam` `main` at `v0.90.0`
(`f2db2b0`). The TailCam rename/migration, Orion UI, video wall, command palette,
current update flow, current security middleware, local Ollama analyzer,
timelapse stack, model/training pages, printer timelapse, and print-failure
detection are already implemented.

---

## Locked Interfaces

### Node Management API

Direct node-management endpoints live under `/api/v1/node` and operate on the
receiving node only. Fleet dispatch uses the explicit allowlisted relay below;
never send node-management writes through the generic proxy.

```text
GET  /api/v1/node/capabilities
GET  /api/v1/node/health
GET  /api/v1/node/audit?limit=100&offset=0
POST /api/v1/node/actions/reload
```

The node opened by the browser also exposes a narrowly allowlisted fleet relay:

```text
GET  /api/v1/fleet/nodes/{node_key}/capabilities
GET  /api/v1/fleet/nodes/{node_key}/health
GET  /api/v1/fleet/nodes/{node_key}/audit?limit=100&offset=0
POST /api/v1/fleet/nodes/{node_key}/actions/reload
```

`node_key=local` dispatches locally. Remote keys resolve only through
`ClusterService`; the relay can call only the four matching `/api/v1/node`
contracts, never an arbitrary path or method. The existing generic proxy rejects
all `/api/v1/node` and `/api/v1/fleet` paths. A remote mutation records both a
coordinator dispatch audit event and a receiving-node execution audit event.

`NodeCapabilities` reports API version, platform, supported features/actions, and whether TailCam received a verified Tailscale identity/capability context for the request.

`NodeHealth` reports node identity/version/platform, process uptime, Tailscale/Serve state, camera totals/status, recording count, local media bytes, update state, AI state, and health issues.

Phase 1 exposes only the safe process-local `reload` action. Service lifecycle, app update, retention cleanup, reboot, and shutdown use the same action/audit architecture in Phase 2 after persistent job/recovery support exists.

### Authorization

Use the single Tailscale application capability:

```text
factshin.github.io/cap/tailcam
```

Capability values use:

```json
{"roles":["viewer","operator","admin"]}
```

Personal-mode behavior:

- Loopback requests are verified local admin.
- Tailscale Serve requests with `Tailscale-User-Login` are verified Personal-mode admin; this is a TailCam product role, not a claim that the user is a Tailscale ACL administrator.
- Tagged-node requests without a user identity require the app capability header.
- Requests without loopback origin, Serve identity, or accepted app capability may read existing viewer endpoints but cannot call `/api/v1/node/*` mutations.
- Team/Professional role restrictions are deferred, but role parsing and audit actor fields are implemented now.

`tailcam tailscale serve` must add:

```bash
tailscale serve --bg --accept-app-caps=factshin.github.io/cap/tailcam --https=<port> localhost:<port>
```

### Desktop Runtime

- Main window loads `http://127.0.0.1:<configured-port>/`.
- Menu-bar Command Center loads `http://127.0.0.1:<configured-port>/desktop/command-center`.
- The Tauri process probes `/api/system`. If a compatible node responds, it attaches without spawning another process.
- If nothing responds, it launches the bundled `tailcam-node` sidecar with `run`; the node's normal config controls its local port and `tailscale.auto_serve`, so default desktop startup exposes TailCam through Tailscale Serve when Tailscale is available.
- If the port is occupied by a non-TailCam service, show a native error and do not replace or kill the process.
- Closing the main window hides it; quitting from the menu-bar command exits the app and stops only a sidecar owned by that app instance.
- Autostart launches TailCam hidden with the menu bar active.
- Tauri capabilities permit remote IPC only from `http://127.0.0.1:*`, only for the `main` and `command-center` windows, and only for the custom `open_main_window`, `quit_tailcam`, `get_launch_at_login`, and `set_launch_at_login` commands.
- Sidecar execution, notification delivery, tray behavior, and plugin APIs stay in Rust and are never directly exposed to the loopback web origin.

---

### Task 1: Establish Current Baseline And Tooling

**Files:**
- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Modify: `web-ui/package.json`
- Create: `rust-toolchain.toml`
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create an isolated worktree from current TailCam main**

Run:

```bash
git fetch origin main
mkdir -p ~/.config/superpowers/worktrees/TailCam
git worktree add ~/.config/superpowers/worktrees/TailCam/desktop-alpha -b feat/desktop-alpha f2db2b0836977a4a9a45ee344953f3f82fac480f
cd ~/.config/superpowers/worktrees/TailCam/desktop-alpha
```

Expected: worktree starts exactly at TailCam `v0.90.0` commit `f2db2b0`.

- [ ] **Step 2: Install and verify the required local toolchains**

Run on macOS:

```bash
brew install python@3.12 rustup
rustup default stable
python3.12 --version
rustc --version
node --version
```

Expected: Python 3.12, stable Rust, and Node 20 or newer are available.

- [ ] **Step 3: Verify the existing baseline before edits**

Run:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
cd web-ui && npm ci && npm run typecheck && npm run build
```

Expected: `145` or more Python tests pass; Ruff, mypy, TypeScript, and Vite build pass.

- [ ] **Step 4: Add reproducible desktop toolchain metadata**

Add `desktop/src-tauri/target/`, `desktop/node_modules/`, and `desktop/sidecars/dist/` to `.gitignore`.

Add a `desktop` optional dependency group to `pyproject.toml`:

```toml
desktop = ["pyinstaller==6.20.0"]
```

Add exact desktop scripts/dependencies to `web-ui/package.json`; keep `@tauri-apps/api`
in `dependencies` and the CLI in `devDependencies`:

```json
"desktop:dev": "tauri dev --config ../desktop/src-tauri/tauri.conf.json",
"desktop:build": "tauri build --config ../desktop/src-tauri/tauri.conf.json",
"@tauri-apps/api": "2.11.0",
"@tauri-apps/cli": "2.11.2"
```

Create `rust-toolchain.toml`:

```toml
[toolchain]
channel = "stable"
components = ["clippy", "rustfmt"]
profile = "minimal"
```

- [ ] **Step 5: Add CI gates without release secrets**

Create `.github/workflows/ci.yml` with:

- Ubuntu: Python tests/Ruff/mypy and web typecheck/build.
- macOS: `cargo check`, `cargo test`, `cargo clippy -- -D warnings`, and PyInstaller sidecar smoke build.
- Windows: `cargo check` and `cargo test` for the shared desktop crate.
- No signing, notarization, or publishing in this workflow.

- [ ] **Step 6: Commit the tooling baseline**

```bash
git add .gitignore pyproject.toml web-ui/package.json web-ui/package-lock.json rust-toolchain.toml .github/workflows/ci.yml
git commit -m "build: establish desktop toolchain and CI"
```

### Task 2: Add Request Identity And Role Parsing

**Files:**
- Create: `src/tailcam/security/__init__.py`
- Create: `src/tailcam/security/principal.py`
- Create: `tests/test_principal.py`

- [ ] **Step 1: Write failing principal parsing tests**

Cover:

```python
def test_loopback_is_local_admin(): ...
def test_serve_user_is_verified_personal_admin(): ...
def test_app_capability_roles_are_parsed(): ...
def test_tagged_node_without_capability_is_not_admin(): ...
def test_spoofed_headers_on_non_loopback_request_are_rejected(): ...
```

Use Starlette `Headers`, explicit client host values, RFC2047-encoded header input, and capability JSON shaped as:

```json
{"factshin.github.io/cap/tailcam":[{"roles":["viewer","operator","admin"]}]}
```

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
.venv/bin/pytest tests/test_principal.py -q
```

Expected: FAIL because `tailcam.security.principal` does not exist.

- [ ] **Step 3: Implement the principal model and parser**

Implement:

```python
class TailCamRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

@dataclass(frozen=True)
class RequestPrincipal:
    actor: str
    display_name: str | None
    source: Literal["local", "tailscale-user", "tailscale-node", "unverified"]
    verified: bool
    roles: frozenset[TailCamRole]

def principal_from_request(request: Request) -> RequestPrincipal: ...
```

Only trust Serve identity/app-capability headers when the backend connection is loopback, because TailCam binds to localhost and Tailscale Serve strips spoofed incoming headers.

- [ ] **Step 4: Run principal tests**

```bash
.venv/bin/pytest tests/test_principal.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tailcam/security tests/test_principal.py
git commit -m "feat: parse TailCam Tailscale principals"
```

### Task 3: Add Audit Persistence

**Files:**
- Modify: `src/tailcam/persistence/models.py`
- Modify: `src/tailcam/persistence/store.py`
- Create: `src/tailcam/management/audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write failing audit-store tests**

Test ordered insertion/listing, JSON metadata round-trip, actor/source/result fields, and limit/offset pagination.

Expected record:

```python
AuditRecord(
    id=None,
    created_ts=123.0,
    actor="alice@example.com",
    source="tailscale-user",
    action="node.reload",
    target="office-mac",
    result="success",
    detail="capture workers reloaded",
    metadata_json='{"camera_count":2}',
)
```

- [ ] **Step 2: Run focused tests and verify failure**

```bash
.venv/bin/pytest tests/test_audit.py -q
```

- [ ] **Step 3: Add schema migration and audit service**

Advance the SQLite schema version and add:

```sql
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts REAL NOT NULL,
  actor TEXT NOT NULL,
  source TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL,
  result TEXT NOT NULL,
  detail TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(created_ts DESC);
```

`AuditLog.record(...)` writes one event; `AuditLog.list(...)` returns newest first.

- [ ] **Step 4: Run focused and migration regression tests**

```bash
.venv/bin/pytest tests/test_audit.py tests/test_rename_migration.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/tailcam/persistence src/tailcam/management tests/test_audit.py
git commit -m "feat: persist TailCam management audit events"
```

### Task 4: Add Explicit Node Health And Capability Services

**Files:**
- Create: `src/tailcam/management/__init__.py`
- Create: `src/tailcam/management/health.py`
- Create: `src/tailcam/management/capabilities.py`
- Create: `tests/test_management_health.py`

- [ ] **Step 1: Write failing service tests**

Cover a healthy synthetic node, an offline camera issue, stopped Tailscale, unavailable AI, update available, and stable capability identifiers.

Required capability identifiers:

```python
{
    "camera.view", "camera.control", "camera.record",
    "node.health", "node.reload", "node.audit",
    "ai.ollama.status",
}
```

- [ ] **Step 2: Implement focused immutable result models**

Implement dataclasses for:

```python
NodeIssue(code, severity, summary, detail)
NodeHealthSnapshot(...)
NodeCapabilitySet(api_version="1", capabilities=frozenset(...), actions=frozenset({"reload"}))
```

`NodeHealthService` depends on `AppContext` collaborators but does not import FastAPI.

- [ ] **Step 3: Verify services**

```bash
.venv/bin/pytest tests/test_management_health.py -q
```

- [ ] **Step 4: Commit**

```bash
git add src/tailcam/management tests/test_management_health.py
git commit -m "feat: add explicit node health and capability services"
```

### Task 5: Expose `/api/v1/node` With Authorization And Audit

**Files:**
- Create: `src/tailcam/web/routes_node_v1.py`
- Create: `src/tailcam/web/routes_fleet_v1.py`
- Modify: `src/tailcam/web/app.py`
- Modify: `src/tailcam/web/context.py`
- Modify: `src/tailcam/web/routes_proxy.py`
- Modify: `src/tailcam/web/schemas.py`
- Modify: `src/tailcam/cluster/service.py`
- Create: `tests/test_node_api.py`
- Create: `tests/test_fleet_api.py`

- [ ] **Step 1: Write failing API contract and authorization tests**

Cover:

- Capabilities and health response shapes.
- Audit pagination.
- Local loopback `POST /api/v1/node/actions/reload` succeeds.
- Verified Tailnet Personal-mode request succeeds.
- Unverified mutation returns `403`.
- Reload writes success/failure audit records.
- Fleet relay dispatches only allowlisted node-management contracts.
- Remote reload records coordinator dispatch and remote execution audit events.
- Generic `/proxy/{key}/api/v1/node/*` and `/proxy/{key}/api/v1/fleet/*` requests are rejected.
- Generic proxy and fleet relay never forward caller-supplied `Tailscale-*` identity/capability headers.
- Existing `/api/*` behavior remains unchanged.

- [ ] **Step 2: Add Pydantic v1 node schemas**

Define:

```python
class NodeCapabilitiesInfo(BaseModel): ...
class NodeHealthInfo(BaseModel): ...
class NodeIssueInfo(BaseModel): ...
class PrincipalInfo(BaseModel): ...
class AuditEventInfo(BaseModel): ...
class NodeActionResponse(BaseModel): ...
```

- [ ] **Step 3: Implement router dependencies and endpoints**

Use dependencies:

```python
def get_principal(request: Request) -> RequestPrincipal: ...
def require_admin(principal: RequestPrincipal = Depends(get_principal)) -> RequestPrincipal: ...
```

`reload` reuses the current process-local reload behavior, returns updated health,
and always records an audit event.

The fleet router accepts `local` or a discovered `ClusterService` peer key. It
maps each route to one fixed upstream method/path, applies the coordinator's
authorization dependency before dispatch, uses bounded timeouts, preserves
upstream status/details, and audits remote mutations. It does not forward incoming
identity/capability headers; the receiving node trusts only headers added by its
own Tailscale Serve hop. Never accept a caller-provided upstream URL or path.

Reject management API paths in `routes_proxy.py` before building an upstream
request and strip every caller-supplied `Tailscale-*` header from other proxied
requests.

- [ ] **Step 4: Run API and security regressions**

```bash
.venv/bin/pytest tests/test_node_api.py tests/test_fleet_api.py tests/test_api.py tests/test_cluster.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/tailcam/web src/tailcam/cluster src/tailcam/management tests/test_node_api.py tests/test_fleet_api.py
git commit -m "feat: expose authorized node management API"
```

### Task 6: Forward Tailscale App Capabilities Through Serve

**Files:**
- Modify: `src/tailcam/tailscale/client.py`
- Modify: `tests/test_tailscale.py`
- Modify: `README.md`
- Create: `docs/tailscale-grants.md`

- [ ] **Step 1: Write failing Serve command tests**

Assert that `serve()` invokes:

```python
[
    "serve", "--bg",
    "--accept-app-caps=factshin.github.io/cap/tailcam",
    "--https=8443",
    "localhost:8088",
]
```

Also test that an older Tailscale CLI without `--accept-app-caps` still starts
Serve without the flag and reports app-capability authorization as unavailable.

- [ ] **Step 2: Implement the Serve flag and capability constant**

Add:

```python
TAILCAM_APP_CAPABILITY = "factshin.github.io/cap/tailcam"
```

Use it in `TailscaleClient.serve`.

Detect support from `tailscale serve --help`. Tailscale 1.92 or newer receives
`--accept-app-caps`; older versions retain private Serve access without app
capabilities and surface a health warning that tagged-node administration
requires an upgrade.

- [ ] **Step 3: Document grants**

Provide Personal, operator, and admin examples. Personal example grants `admin` to
the owner's user identity; tagged-node example grants `admin` between
`tag:tailcam` nodes. State the Tailscale 1.92 minimum for application capability
authorization.

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest tests/test_tailscale.py tests/test_principal.py tests/test_node_api.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/tailcam/tailscale tests/test_tailscale.py README.md docs/tailscale-grants.md
git commit -m "feat: forward TailCam app capabilities through Tailscale Serve"
```

### Task 7: Add Shared Typed Fleet Client And Fleet Workspace

**Files:**
- Modify: `web-ui/src/types.ts`
- Modify: `web-ui/src/api/client.ts`
- Modify: `web-ui/src/api/hooks.ts`
- Modify: `web-ui/src/app/AppShell.tsx`
- Modify: `web-ui/src/main.tsx`
- Create: `web-ui/src/screens/Fleet.tsx`
- Create: `web-ui/src/components/NodeHealthCard.tsx`
- Modify: `web-ui/src/styles.css`

- [ ] **Step 1: Add frontend test tooling and failing contract tests**

Add a `test` script plus exact `vitest@4.1.8`, `jsdom@29.1.1`,
`@testing-library/react@16.3.2`, and `@testing-library/jest-dom@6.9.1`
dev dependencies. Test node URL building and health severity aggregation without
rendering live streams.

- [ ] **Step 2: Add exact TypeScript API mirrors**

Add `PrincipalInfo`, `NodeCapabilitiesInfo`, `NodeIssueInfo`, `NodeHealthInfo`, `AuditEventInfo`, and `NodeActionResponse`.

Add `node_key` to `HostInfo`; use `"local"` for the receiving node and the
discovered peer key for every remote node.

Client rules:

```ts
getFleetNodeHealth(nodeKey: string)
getFleetNodeCapabilities(nodeKey: string)
getFleetNodeAudit(nodeKey: string, limit?: number, offset?: number)
reloadFleetNode(nodeKey: string)
```

Every function calls the explicit `/api/v1/fleet/nodes/{nodeKey}` relay. Existing
`proxy_prefix` remains only for current camera/media/event behavior.

- [ ] **Step 3: Implement Fleet workspace**

Add `/fleet` navigation and a node card per `HostInfo`. Each card shows reachability, version/version drift, cameras, recordings, storage, Tailscale, AI, authorization context, issues, and a confirmed Reload action.

Fleet must render partial failures per node and never block healthy cards on an unreachable node.

- [ ] **Step 4: Verify frontend**

```bash
cd web-ui
npm test -- --run
npm run typecheck
npm run build
```

- [ ] **Step 5: Commit source and rebuilt SPA**

```bash
git add web-ui src/tailcam/web/spa
git commit -m "feat: add TailCam fleet health workspace"
```

### Task 8: Add Desktop-Aware Web Routes

**Files:**
- Create: `web-ui/src/desktop/runtime.ts`
- Create: `web-ui/src/screens/DesktopCommandCenter.tsx`
- Modify: `web-ui/src/main.tsx`
- Modify: `web-ui/src/styles.css`
- Create: `web-ui/src/desktop/runtime.test.ts`

- [ ] **Step 1: Implement a browser-safe desktop runtime adapter**

Expose:

```ts
export interface DesktopRuntime {
  isDesktop: boolean;
  openMainWindow(): Promise<void>;
  quit(): Promise<void>;
  getLaunchAtLogin(): Promise<boolean>;
  setLaunchAtLogin(enabled: boolean): Promise<void>;
}
```

The browser implementation is a no-op/fallback; the Tauri implementation dynamically
imports `@tauri-apps/api/core`. It may invoke only `open_main_window`,
`quit_tailcam`, `get_launch_at_login`, and `set_launch_at_login`.

- [ ] **Step 2: Build `/desktop/command-center`**

The compact route includes:

- Two low-bandwidth live previews.
- Fleet healthy/warning/offline counts.
- Active recording count and newest event.
- Snapshot/record quick actions.
- Open TailCam and Quit commands.

It must fit a `390x560` menu-bar panel and use existing camera/event hooks.

- [ ] **Step 3: Verify route and browser fallback**

```bash
cd web-ui
npm test -- --run
npm run typecheck
npm run build
```

- [ ] **Step 4: Commit**

```bash
git add web-ui src/tailcam/web/spa
git commit -m "feat: add desktop command center route"
```

### Task 9: Build The TailCam Node Sidecar

**Files:**
- Create: `desktop/sidecars/tailcam-node.spec`
- Create: `desktop/scripts/build-sidecar.py`
- Create: `desktop/scripts/smoke-sidecar.py`
- Modify: `pyproject.toml`
- Create: `tests/test_desktop_sidecar.py`

- [ ] **Step 1: Write the sidecar manifest test**

Test that the PyInstaller spec includes the `tailcam` package, bundled SPA/static/templates, and a console-free macOS/Windows executable entry point.

- [ ] **Step 2: Implement deterministic sidecar build**

`build-sidecar.py` must:

- Run PyInstaller from the repository root.
- Name output `tailcam-node-<tauri-target-triple>`.
- Include TailCam package data.
- Refuse cross-compilation; build on each target OS.
- Print the final sidecar path for CI.

- [ ] **Step 3: Add smoke verification**

`smoke-sidecar.py` launches:

```bash
TAILCAM_SYNTHETIC=1 TAILCAM_DATA_DIR=<temp> TAILCAM_CONFIG_DIR=<temp> tailcam-node run --no-tailscale --port <free-port>
```

The smoke test alone uses `--no-tailscale` so CI never mutates host Tailscale
configuration. It waits for `/api/system`, verifies `/`, `/api/cameras`, and
`/api/v1/node/health`, then terminates the owned process.

- [ ] **Step 4: Run smoke build**

```bash
.venv/bin/pip install -e ".[dev,desktop]"
.venv/bin/python desktop/scripts/build-sidecar.py
.venv/bin/python desktop/scripts/smoke-sidecar.py
```

- [ ] **Step 5: Commit**

```bash
git add desktop pyproject.toml tests/test_desktop_sidecar.py
git commit -m "build: package TailCam node sidecar"
```

### Task 10: Scaffold The Cross-Platform Tauri Shell

**Files:**
- Create: `desktop/src-tauri/Cargo.toml`
- Create: `desktop/src-tauri/build.rs`
- Create: `desktop/src-tauri/tauri.conf.json`
- Create: `desktop/src-tauri/capabilities/default.json`
- Create: `desktop/src-tauri/src/main.rs`
- Create: `desktop/src-tauri/src/lib.rs`
- Create: `desktop/src-tauri/src/node.rs`
- Create: `desktop/src-tauri/src/windows.rs`
- Create: `desktop/src-tauri/icons/*`

- [ ] **Step 1: Add failing Rust node-lifecycle tests**

Test pure decisions for:

```rust
enum NodeDisposition { Attach, Spawn, PortConflict }
fn disposition(probe: ProbeResult) -> NodeDisposition
```

Also test that quitting stops only an owned sidecar.

- [ ] **Step 2: Implement app configuration**

Use bundle identifier `io.github.factshin.tailcam`, product name `TailCam`, main window hidden until the node is ready, updater artifacts enabled, and the target-triple sidecar configured.

Add Tauri, `tauri-plugin-autostart`, `tauri-plugin-notification`, and
`tauri-plugin-updater` as Rust dependencies and commit `Cargo.lock`. Do not add
the shell plugin: spawn the one fixed bundled sidecar from Rust so no general
command execution capability exists.

Configure `capabilities/default.json` with:

```json
{
  "identifier": "tailcam-loopback",
  "windows": ["main", "command-center"],
  "remote": { "urls": ["http://127.0.0.1:*"] },
  "permissions": [
    "allow-open-main-window",
    "allow-quit-tailcam",
    "allow-get-launch-at-login",
    "allow-set-launch-at-login"
  ]
}
```

Do not grant core-default or plugin permissions to the remote loopback origin.

- [ ] **Step 3: Implement node attach/spawn lifecycle**

`node.rs` probes `/api/system`, validates the response contains TailCam version/host fields, spawns the bundled sidecar only when absent, waits with bounded backoff, and exposes owned-process shutdown.

Record the exact validated TailCam origin after a successful probe and add a
navigation handler that rejects any `main` or `command-center` navigation to a
different origin. The static capability uses a wildcard port only because the
TailCam port is configurable.

- [ ] **Step 4: Implement shared window commands**

Register:

```rust
#[tauri::command] fn open_main_window(...)
#[tauri::command] fn quit_tailcam(...)
#[tauri::command] fn get_launch_at_login(...)
#[tauri::command] fn set_launch_at_login(...)
```

Closing the main window hides it.

- [ ] **Step 5: Verify Rust**

```bash
cd desktop/src-tauri
cargo fmt --check
cargo test
cargo clippy -- -D warnings
cargo check
```

- [ ] **Step 6: Commit**

```bash
git add desktop/src-tauri
git commit -m "feat: scaffold cross-platform TailCam desktop shell"
```

### Task 11: Add macOS Menu-Bar Command Center And Native Notifications

**Files:**
- Create: `desktop/src-tauri/src/tray.rs`
- Create: `desktop/src-tauri/src/notifications.rs`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src-tauri/tauri.conf.json`
- Modify: `desktop/src-tauri/capabilities/default.json`

- [ ] **Step 1: Add tray/window state tests**

Test menu actions and stable window labels:

```text
main
command-center
```

- [ ] **Step 2: Implement tray behavior**

Clicking the TailCam menu-bar icon toggles a borderless `390x560` Command Center window positioned near the tray. Menu items provide Open TailCam and Quit TailCam.

- [ ] **Step 3: Implement notification polling**

Poll local `/api/events?scope=local&limit=1` only while the app owns/attaches to a healthy local node. Notify once per new event ID; clicking the notification opens the main app at `/events`.

- [ ] **Step 4: Add autostart**

Use Tauri's autostart plugin to launch hidden after the user enables “Launch TailCam at login” in the desktop app. Do not silently enable it during development builds.

- [ ] **Step 5: Verify macOS desktop dev loop**

```bash
.venv/bin/python desktop/scripts/build-sidecar.py
cd web-ui && npm run desktop:dev
```

Acceptance:

- Menu-bar icon appears.
- Command Center opens and streams low-bandwidth previews.
- Open/close/hide behavior is correct.
- Existing installed TailCam node is attached without duplicate spawn.
- No installed node causes sidecar spawn.
- New local event creates one native notification.

- [ ] **Step 6: Commit**

```bash
git add desktop/src-tauri web-ui src/tailcam/web/spa
git commit -m "feat: add macOS TailCam command center"
```

### Task 12: Desktop Packaging, Documentation, And Alpha Verification

**Files:**
- Create: `desktop/README.md`
- Create: `docs/desktop-alpha.md`
- Create: `.github/workflows/desktop-alpha.yml`
- Modify: `README.md`
- Modify: `src/tailcam/__init__.py`
- Modify: `web-ui/package.json`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Document exact developer and alpha flows**

Document Rust/Python/Node prerequisites, sidecar build, desktop dev, unsigned
smoke build, signed/notarized alpha release, updater key rotation/recovery,
interaction with an existing CLI-installed node, Tailscale grants, and Windows
parity status.

- [ ] **Step 2: Add alpha artifact and signed release workflow**

Create `.github/workflows/desktop-alpha.yml` with two manually selected modes:

- `smoke`: build the sidecar and unsigned Tauri `.app`/DMG artifact without
  publishing or requiring secrets.
- `release`: require Apple signing/notarization credentials and the Tauri updater
  signing key, build/notarize the `.app`/DMG, emit updater signatures and
  `latest.json`, then publish a draft GitHub prerelease for human verification.

Keep all secrets out of the normal CI workflow and fail the release mode before
building when required secrets are absent.

- [ ] **Step 3: Bump version**

Advance `src/tailcam/__init__.py` from `0.90.0` to `0.91.0`. Set the Tauri
package and `web-ui/package.json` versions to `0.91.0`; keep web/desktop
displayed versions synchronized from `/api/system`.

- [ ] **Step 4: Run the complete verification matrix**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
cd web-ui && npm test -- --run && npm run typecheck && npm run build
cd ../desktop/src-tauri && cargo fmt --check && cargo test && cargo clippy -- -D warnings && cargo check
cd ../.. && .venv/bin/python desktop/scripts/build-sidecar.py && .venv/bin/python desktop/scripts/smoke-sidecar.py
bash -n install-linux.sh install-macos.sh uninstall-linux.sh uninstall-macos.sh
git diff --check
```

- [ ] **Step 5: Run a two-node synthetic fleet acceptance test**

Start two isolated TailCam nodes, configure one static peer, and verify:

- Fleet screen shows both nodes and partial failures.
- Current camera/media/event aggregation still works.
- `/api/v1/node/health` works directly and through the explicit fleet relay.
- Generic proxy attempts to reach node/fleet management routes are rejected.
- Unverified management mutation fails; verified Personal-mode mutation succeeds.
- Desktop app attaches to node A and controls node B through the fleet UI.

- [ ] **Step 6: Commit**

```bash
git add README.md desktop docs .github/workflows src/tailcam/__init__.py src/tailcam/web/spa web-ui/package.json web-ui/package-lock.json
git commit -m "docs: prepare TailCam macOS desktop alpha"
```

---

## Phase 1 Acceptance Criteria

- The current TailCam PWA and all existing APIs remain functional.
- `/api/v1/node/capabilities`, `/health`, `/audit`, and authorized `/actions/reload` have tested stable response contracts.
- The explicit `/api/v1/fleet/nodes/{node_key}` relay supports the same allowlisted
  contracts and no arbitrary management proxying.
- TailCam parses verified Tailscale Serve identity/app-capability headers and audits management actions.
- Fleet workspace renders every node independently, including unreachable nodes and version drift.
- macOS app attaches to an existing TailCam node or launches its bundled sidecar without duplicating services.
- Menu-bar Command Center provides live previews, health, recent events, quick actions, Open, and Quit.
- Native notifications and optional launch-at-login work on macOS.
- Rust shared modules compile on Windows CI from the first phase.
- Secret-free smoke artifacts build in CI; the public-alpha gate requires a
  signed/notarized draft prerelease with a verified updater signature.
- Python, web, Rust, sidecar smoke, two-node synthetic fleet, and script verification all pass.
