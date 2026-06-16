# TailCam Desktop Product Roadmap

> **For agentic workers:** Each phase gets its own implementation plan and must ship as a usable, tested vertical slice. Do not combine phases into one branch.

**Baseline:** `FactShin/TailCam` `main` at `v0.90.0` (`f2db2b0`).

**Product goal:** Build a Tailscale-first hybrid command center with shared macOS/Windows product code, genuinely native platform surfaces, complete fleet administration, premium monitoring, local AI, and automations.

## Phase 1: macOS Desktop Alpha And Fleet Foundation

Ship a useful macOS menu-bar application around the current TailCam product.

- Add explicit `/api/v1/node/*` health/capability contracts and a shared typed client.
- Parse Tailscale Serve identity and app-capability headers; preserve effortless Personal mode while creating the authorization boundary for Team/Professional modes.
- Add audit foundations for management actions.
- Add a Fleet workspace showing every node's version, health, storage, AI state, camera state, and authorization status.
- Add a Tauri 2 desktop shell that attaches to an existing local TailCam service or starts a bundled PyInstaller node sidecar.
- Add the macOS menu-bar Command Center, native notifications, launch-at-login, main-window lifecycle, and packaging/check pipelines.
- Start Windows parity immediately with cross-platform Rust modules and Windows compile checks.

**Exit gate:** A signed/notarized macOS alpha starts TailCam, exposes the full current UI, operates from the menu bar, shows fleet health, works locally without Tailscale, verifies remote identities over Tailscale, and can verify a signed updater artifact.

## Phase 2: Full Fleet Operations And Premium Monitoring

- Add persistent management jobs for remote config, service lifecycle, update, retention cleanup, support bundles, reboot, and shutdown.
- Add config snapshots/diffs/rollback, action previews, staged fleet rollout, partial-failure reporting, and full audit history.
- Add saved layouts, detachable/floating monitors, multi-window camera walls, global shortcuts, and instant replay.
- Replace the broad generic write proxy with explicit fleet-management calls while retaining streaming/media proxying.

**Exit gate:** Every TailCam node can be administered like a local node with preview, confirmation, audit, health verification, and recovery.

## Phase 3: AI Studio And Event Intelligence

- Generalize the current Ollama analyzer into versioned provider/model/runtime contracts.
- Add hardware profiling, model catalog/import/download, model deployment over Tailscale, activation, rollback, and resource telemetry.
- Add per-camera zones/classes/confidence/schedules and a recorded-footage evaluation lab.
- Add unified review queue, labels/tags/bookmarks, summaries, explainable detections, and natural-language metadata search.
- Add optional cloud/BYOK providers behind explicit privacy and egress controls.

**Exit gate:** The user can evaluate, tune, deploy, inspect, and roll back local AI across the fleet without raw video leaving the tailnet by default.

## Phase 4: Automations And Windows Release

- Add the node-resident trigger-condition-action engine, schedules, execution history, retries, test runs, and exported recipes.
- Add notifications, webhooks, Apple Shortcuts, Home Assistant, OctoPrint/Moonraker, and provider credentials.
- Complete the Windows native system-tray Command Center, notifications, launch/background behavior, installer, signing, and updater.
- Activate Trusted Team and Professional governance surfaces over the established Tailscale capability/audit model.

**Exit gate:** Windows reaches feature parity with macOS, and TailCam can operate as a programmable multi-site platform without compromising the Personal-mode default.

## Cross-Phase Rules

- Keep the current PWA fully functional; desktop-only behavior goes through a runtime adapter.
- Prefer explicit, versioned management endpoints over adding writes to `/proxy/{key}`.
- Local controls remain usable without Tailscale; remote/fleet operations fail closed when identity cannot be verified.
- Build every backend capability synthetic-camera-first and cross-platform.
- Add Windows compile/parity checks in Phase 1 rather than postponing portability work.
- Bump `src/tailcam/__init__.py` with every release merged to `main`.
