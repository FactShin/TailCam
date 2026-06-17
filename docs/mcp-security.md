# TailCam MCP — security & audit model

TailCam MCP is designed so read operations are easy and write operations are
explicit, bounded, and audited. This document describes the transport trust
model, identity, confirmation rules, and the audit trail.

## Principles

1. Tailscale remains the network and identity backbone.
2. MCP wraps existing TailCam APIs; it never bypasses them.
3. Read is easy; writes are explicit, confirmed, and audited.
4. Local and remote agents share one server core.
5. Fleet actions are powerful but never generic arbitrary proxying.
6. There is no "call arbitrary URL", arbitrary management proxy, or shell tool.

## Transports and identity

### Local stdio (`tailcam mcp stdio`)

Local stdio follows TailCam's personal-computer trust model: anyone who can
launch the process can talk to the local node, and acts as the local **admin**.
MCP clients still surface their own per-tool approvals, and TailCam still enforces
its own write confirmations. Privileged v1 actions (e.g. node reload) are audited
by the node itself.

### Remote Streamable HTTP (`/mcp`)

Remote HTTP MCP is **fail-closed**:

- The caller is parsed with TailCam's principal parser. Tailscale Serve identity
  headers are trusted **only on loopback** (Serve → `127.0.0.1`); the server runs
  uvicorn with `proxy_headers=False` so a forwarded address can't spoof that
  anchor.
- Unverified callers are rejected with HTTP 401 before any tool runs.
- Read tools require a verified local or Tailscale principal.
- Admin tools require the TailCam `admin` role.
- Fleet writes require `admin` **plus** an explicit confirmation string.
- Every state-changing action records an audit event.

The endpoint is mounted only when both `[mcp] enabled` and `[mcp] http_enabled`
are true, and is reached over Tailscale Serve — never the public internet.

## Roles

Tools declare a minimum role; `tools/list` is filtered to what the caller may
use:

| Role | Can call |
| --- | --- |
| `viewer` | read tools and read-only workflows |
| `operator` | + camera actions (snapshot, recording, motion, settings, restart) |
| `admin` | + node/fleet reloads, AI/training writes, audit log |

## Confirmation rules

State-changing tools accept `confirm` or `confirm_scope` only where needed.
Confirmation is master-switched by `[mcp] require_confirm_for_writes` and
`[mcp] require_confirm_for_fleet_writes`.

| Tool | Requirement |
| --- | --- |
| `capture_snapshot` | none |
| `start_recording` / `stop_recording` | none (single camera) |
| `set_motion_detection` / `update_camera_settings` | none |
| `restart_camera` | `confirm=true` |
| `set_ai_config` | `confirm=true` |
| `import_events_to_dataset` | `confirm=true` |
| `reload_node` | `confirm_scope="reload:<node_key>"` |
| `reload_fleet_nodes` | `confirm_scope="reload:fleet:<count>"` |

Use `prepare_fleet_admin_plan` to get the exact confirm strings for a goal before
executing anything.

## Audit trail

Every state-changing tool records an audit event (visible via `get_audit_log` and
`tailcam://audit/recent`). Over HTTP the audit captures:

- MCP transport (`stdio` or `streamable_http`)
- MCP client name (when provided at initialize)
- tool name and target node/camera
- the confirmation string's effect (recorded as metadata, never secrets)
- the principal's actor/source/role
- result and, on failure, the normalized error code

Read-only tools do not spam the audit log.

## Error model

Failures are normalized so agents see a stable code:

```json
{ "ok": false, "error": { "code": "tailcam.peer_unreachable",
  "message": "peer-node is unreachable", "retryable": true, "status_code": 502 } }
```

Codes: `tailcam.not_running`, `tailcam.unauthorized`, `tailcam.admin_required`,
`tailcam.confirmation_required`, `tailcam.node_unknown`, `tailcam.camera_unknown`,
`tailcam.camera_unavailable`, `tailcam.peer_unreachable`,
`tailcam.invalid_request`, `tailcam.invalid_response`, `tailcam.timeout`,
`tailcam.unsupported_transport`.

## Non-goals

- No public internet exposure outside Tailscale.
- No arbitrary shell execution.
- No arbitrary HTTP proxy or arbitrary TailCam endpoint caller.
- No automatic fleetwide destructive action without an exact confirmation string.
- No cloud model dependency inside TailCam MCP.
