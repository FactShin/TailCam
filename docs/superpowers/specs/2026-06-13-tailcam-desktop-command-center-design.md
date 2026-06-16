# TailCam Desktop Command Center Design

**Date:** 2026-06-13  
**Status:** Approved for implementation planning

## Summary

TailCam will become a Tailscale-first, local-first camera platform with a dedicated
macOS application first and a Windows application next. The desktop product is a
hybrid command center: it preserves every current web UI capability while adding
premium live monitoring, complete fleet administration, local AI, event
intelligence, automations, and native operating-system surfaces.

The first user is the project owner, so Personal mode is the frictionless default.
Trusted Team and Professional modes use the same architecture and can be activated
later without rebuilding the product or burdening the initial experience.

The implementation target is `FactShin/TailCam` at the current `main` baseline.

## Implemented Baseline

TailCam `v0.90.0` already provides the renamed `tailcam` package and CLI,
explicit AnyCam data/service migration, current repository URLs, resilient
configuration, self-update, service control, security middleware, camera
delete/restart/reload, cross-host cameras/media/events, a responsive Orion React
PWA, command palette, video wall, desktop Safari fallback, local Ollama motion
analysis, timelapse capture/smoothing/RIFE hooks, live timelapse preview, model
configuration, training data/execution/inference, printer timelapse, and
print-failure detection.

Desktop work must extend those capabilities rather than duplicate them. Existing
web/PWA behavior remains supported while the shared UI gains native desktop
surfaces and explicit fleet-management contracts.

## Product Principles

- **Tailscale is the control and data plane.** Discovery, streams, management
  actions, AI jobs, model distribution, synchronization, and updates travel
  privately node-to-node across the tailnet.
- **Every TailCam node is fully manageable.** Remote nodes expose the same
  versioned management surface as the local node, including configuration,
  diagnostics, logs, storage, service lifecycle, updates, and specific host
  actions such as reboot and shutdown.
- **Local-first and remote fail-closed.** Local cameras and controls remain usable
  when Tailscale is unavailable. Remote and fleet actions require verified
  Tailscale identity and authorization and fail closed.
- **No capability ceiling.** Shared cross-platform code is preferred, but native
  Swift or Windows extensions are added whenever platform APIs provide a better or
  more capable experience.
- **Power with recovery.** Destructive and fleet-wide operations remain available,
  with target/impact previews, confirmations, audit history, configuration
  snapshots, staged rollout, health checks, and rollback.
- **Private AI by default.** AI inference and raw video stay on TailCam nodes by
  default. Optional cloud or bring-your-own-key providers require explicit
  enablement and policies governing data that may leave the tailnet.

## Architecture

### Shared Desktop Core

Use a Tauri 2 desktop shell around a shared React/TypeScript product UI. Reuse and
expand the existing React dashboard rather than maintaining separate SwiftUI and
WinUI screen implementations. The shared desktop core owns navigation, design
system, fleet state, typed API SDK, admin workflows, layouts, AI Studio,
Automations, and cross-platform behavior.

The Tauri layer provides a narrowly defined secure bridge for native capabilities
and coordinates the bundled TailCam node. macOS ships first with native Swift
extensions where needed; Windows follows with equivalent native adapters. The
shared core must never prevent a platform-specific capability.

### TailCam Node Agent

Evolve the existing Python/FastAPI process into the cross-platform TailCam Node
Agent. It retains camera capture, streaming, media, motion, persistence, and
aggregation, then adds a versioned management API for:

- Node health, hardware/camera inventory, storage, versions, and capability flags.
- Complete validated configuration read/write, history, snapshots, and rollback.
- Camera, media, event, retention, and recording administration.
- Diagnostics, structured logs, support bundles, and self-tests.
- Service install/start/stop/restart and application update operations.
- Explicit host reboot and shutdown actions, but no arbitrary remote shell.
- Local AI runtime, model catalog/deployment, inference configuration, and jobs.
- Rules engine, notification routing, integrations, and execution history.
- Audit events for management, host, AI, and automation actions.

The current generic peer proxy must be replaced or constrained by explicit,
versioned node and fleet API clients. Remote management actions use the same
contracts as local actions.

### Tailscale Identity And Authorization

TailCam uses Tailscale identity, grants, and application capabilities as the
foundation for remote authorization. Personal mode automatically grants the owner
full control. Team and Professional modes can later add operators, viewers,
auditors, sites, and policy without inventing a separate network or account layer.

Each management request records actor identity, target node, requested action,
result, and relevant diff/metadata. Remote actions reject requests when identity or
grants cannot be verified.

### Packaging And Native Integration

The macOS application bundles the desktop shell and a self-contained TailCam Node
Agent, installs/controls its per-user background service, handles camera
permissions, supports launch at login, and ships through a signed/notarized update
pipeline. Windows uses the same product core and node contracts with system-tray,
notification, service, signing, packaging, and updater adapters appropriate to
Windows.

## Product Workspaces

### Live

Preserve every current web UI capability: aggregated camera grid, camera detail,
per-view stream controls, global camera controls, snapshots, recordings, gallery,
events, settings, multi-host streams, and resilient offline behavior.

Extend Live with saved layouts, camera walls, detachable and always-on-top floating
monitors, multiple windows, keyboard/global shortcuts, quick actions, instant
replay buffer, richer recording controls, and a low-latency streaming upgrade path.

### Fleet

Provide a fleet overview with node/camera health, reachability, versions, version
drift, storage, AI workload, service state, and actionable warnings. Node detail
offers complete remote parity: configuration, diagnostics, logs, support bundles,
storage cleanup, service lifecycle, application update, and explicit reboot or
shutdown.

Bulk actions support previewing targets and diffs, staged rollout, per-node results,
health verification, rollback, and audit history.

### Events And Media

Replace simple lists with a unified cross-node timeline and review workflow.
Include filtering, search, saved views, bookmarks, tags, review state, clip
creation/export, download and Finder reveal, retention policy, storage analysis,
and evidence/support bundle creation.

Event Intelligence adds motion zones, schedules, class/threshold filtering,
notification routing, local summaries, and natural-language search over structured
event metadata. AI results remain inspectable and link back to source frames,
models, versions, and confidence.

### AI Studio

AI Studio is a first-class subsystem. It provides:

- Pluggable local inference runtimes and a versioned model/provider interface.
- Hardware discovery, node capability benchmarks, and recommended model profiles.
- Model catalog, download/import, checksum/version tracking, deployment,
  activation, rollback, and fleet distribution over Tailscale.
- Per-camera zones, detection classes, confidence thresholds, schedules, resource
  limits, and performance/quality telemetry.
- Recorded-footage evaluation with side-by-side model/profile comparison before
  deploying changes.
- Explainable detections, event summaries, and natural-language metadata search.
- Optional cloud/BYOK providers behind explicit privacy and egress controls.

### Automations

Provide a visual trigger-condition-action rules engine with schedules, camera/AI/
node/event triggers, TailCam actions, notifications, webhooks, Shortcuts, Home
Assistant, test runs, enable/disable controls, execution history, error inspection,
and import/export of recipes.

Rules execute on an assigned node and continue working locally when the desktop app
is closed. Cross-node rules use the tailnet and fail safely when dependencies are
unavailable.

### Admin

Admin contains operating mode, Tailscale access/grants, audit timeline, update
policy, backups/config history, logs, support bundles, notification providers,
integration credentials, privacy/egress controls, feature flags, and recovery
tools.

Personal mode defaults to one owner and no account ceremony. Trusted Team and
Professional mode surfaces remain hidden until enabled.

## Native macOS Experience

The menu bar opens a TailCam Command Center with compact live previews, fleet
health, recent events, current recordings, and high-frequency actions. It can open
the main application, arm/disarm configured monitoring, capture snapshots, start
or stop recordings, and open floating monitors.

The macOS app also includes native notifications with actions, global shortcuts,
launch at login, background operation, native menus, camera permission guidance,
deep links, updater, Finder reveal/export, Dock/menu behavior, and always-on-top
floating monitors. Windows receives equivalent native system-tray and OS surfaces
from the same contracts.

## Error Handling And Recovery

- Remote/fleet operations require Tailscale identity and grants; unavailable or
  unverifiable remote targets fail closed without affecting local operation.
- Every multi-node or destructive action shows target nodes, impact, and relevant
  config/version diff before confirmation.
- Configuration changes create snapshots and support rollback. Updates and bulk
  changes support staged rollout and post-action health verification.
- Management actions expose per-node progress and partial failure results rather
  than collapsing into a single success/failure message.
- Diagnostics produce structured results suitable for UI presentation and support
  bundles. Logs and support bundles redact secrets and sensitive credentials.
- AI deployments retain last-known-good models/profiles and automatically surface
  performance, resource, or health regressions.

## Delivery Phases

### Phase 1: Foundation And Desktop Alpha

- Version the node management API and add Tailscale identity/grant authorization,
  audit foundations, capability discovery, and the typed shared SDK.
- Create the Tauri workspace, bundled Node Agent lifecycle, macOS packaging,
  signing/notarization, updater foundations, permissions, and launch behavior.
- Reuse all existing web UI functionality in the main desktop window.
- Ship the macOS menu-bar Command Center, native notifications, and initial local
  and fleet health views.
- Begin Windows packaging and native-adapter parity tests during this phase.

### Phase 2: Fleet Operations And Premium Monitoring

- Complete remote configuration, diagnostics, logs, support bundles, retention,
  service lifecycle, updates, reboot/shutdown, audit/config history, bulk actions,
  staged rollout, health checks, and rollback.
- Add saved layouts, camera walls, multiple/floating windows, global shortcuts,
  instant replay, and advanced media workflows.

### Phase 3: Local AI And Event Intelligence

- Add inference/model plugin contracts, node hardware profiling, model lifecycle
  and Tailscale distribution, per-camera tuning, evaluation lab, explainable
  results, summaries, review workflow, and search.

### Phase 4: Automations And Windows Release

- Add rules engine, notification/integration providers, Shortcuts, Home Assistant,
  recipes, and execution history.
- Complete Windows-native shell/adapters, system-tray Command Center, packaging,
  signing, updater, installer migration, and full parity verification.
- Expose Trusted Team and Professional governance foundations when ready.

## Testing And Acceptance

- Preserve existing Python tests and add management API contract, authorization,
  audit, migration, rollback, update, host-action, AI plugin, and rules tests.
- Use synthetic cameras and isolated node data directories for deterministic
  end-to-end testing without hardware.
- Maintain a multi-node tailnet lab covering online/offline nodes, version drift,
  partial fleet failures, permission denial, remote fail-closed behavior, model
  distribution, and staged updates.
- Add shared UI unit/component tests, end-to-end desktop flows, accessibility
  checks, and visual regression at supported desktop sizes.
- Test menu-bar/system-tray behavior, notifications, shortcuts, launch at login,
  permissions, floating windows, deep links, signed installation, update,
  rollback, migration, and uninstall.
- Require macOS Alpha acceptance to demonstrate all existing web UI functions,
  menu-bar Command Center, fully authorized remote management foundations, local
  offline operation, and a successful signed install/update path.

## Explicit Decisions

- Product role: Hybrid Command Center.
- Product name and migration: TailCam rename and AnyCam migration are implemented
  in the current baseline and remain covered by regression tests.
- Desktop strategy: shared Tauri 2/React core with native platform extensions.
- macOS menu-bar default: Command Center; floating monitors are also included.
- Administration: full fleet-wide TailCam parity and explicit host reboot/shutdown;
  no arbitrary remote shell.
- Operating modes: Personal default, Trusted Team and Professional architectures
  available progressively.
- AI: local-first with optional cloud/BYOK and explicit egress policies.
- Network behavior: Tailscale-first; local controls work without Tailscale and all
  remote operations fail closed.
- Feature pillars: Fleet Operations, Event Intelligence, Premium Live Monitoring,
  Automations/Integrations, and AI Studio are all in the product roadmap.
