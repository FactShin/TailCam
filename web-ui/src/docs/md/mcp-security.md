# MCP security & audit

The [MCP](mcp-overview) layer is designed so reads are easy and writes are
explicit, bounded, and audited. It reuses TailCam's [principal and role
model](security).

## Transports and trust

### Local stdio (`tailcam mcp stdio`)

Anyone who can launch the process can talk to the local node and acts as the local
**admin** — the personal-computer trust model. Your MCP client still surfaces its
own per-tool approvals, and TailCam still enforces its own write confirmations.

### Remote HTTP (`/mcp`)

Stateless and fail-closed:

- The caller is parsed with TailCam's principal parser **on every request**;
  Tailscale identity is trusted only on loopback (Serve → 127.0.0.1), and the
  server runs with `proxy_headers=false` so a forwarded address can't spoof that
  anchor.
- Unverified callers get **HTTP 401** before any tool runs.
- Read tools require a verified principal; admin tools require the `admin` role.
- Fleet writes require `admin` **plus** an explicit confirmation string.
- Every state-changing action is audited.

The endpoint is mounted only when `mcp.enabled` **and** `mcp.http_enabled` are
true, and is reached over Tailscale — never the public internet.

### No sessions

`/mcp` never issues an `Mcp-Session-Id` and holds nothing between requests. That
is a security property as much as an operational one: there is no session token
to steal, replay, or fixate, and no server-side session whose identity could
drift from the caller's. Authorization is re-derived from the live Tailscale
principal every single request, so a role you revoke in your tailnet takes effect
on that agent's next call — not whenever a session happens to expire.

The header contract:

| Header | Direction | Meaning |
| --- | --- | --- |
| `MCP-Protocol-Version` | request | The revision this request speaks. Absent → `2025-03-26` assumed; unsupported → **400**. |
| `MCP-Protocol-Version` | response | The revision the reply speaks; on `initialize`, the freshly negotiated one. |
| `Mcp-Session-Id` | request | Ignored, so stateful-style clients keep working. |
| `Mcp-Session-Id` | response | Never issued. |

`GET` and `DELETE` answer `405 Allow: POST` — no server-initiated streams, no
session to tear down.

## Confirmation rules

State-changing tools take `confirm` or `confirm_scope` where needed, master-
switched by `mcp.require_confirm_for_writes` and
`mcp.require_confirm_for_fleet_writes`.

| Tool | Requirement |
| --- | --- |
| `capture_snapshot`, recording, motion, settings | none |
| `restart_camera`, `set_ai_config`, `pull_ollama_model` | `confirm=true` |
| `delete_dataset`, `delete_model`, `import_events_to_dataset`, `start_training_run` | `confirm=true` |
| `reload_node` | `confirm_scope="reload:<node_key>"` |
| `reload_fleet_nodes` | `confirm_scope="reload:fleet:<count>"` |

Use `prepare_fleet_admin_plan` to get the exact confirm strings for a goal before
executing.

## Audit trail

Every write records an audit event (visible via `get_audit_log` and
`tailcam://audit/recent`). Over HTTP it captures the MCP transport
(`stdio` / `streamable_http`), the protocol revision, the client name, the tool
and target, the principal's actor/source/role, and the result (or normalized
error). With no session to look the caller up in, the client name comes from the
request itself — the `clientInfo` of an `initialize` sent in that same request,
otherwise its `User-Agent`. Read-only tools don't spam the log.

## Hardening note

The in-process execution path runs as trusted loopback while the MCP layer makes
the authorization decision using the **remote** principal. To prevent a
path-traversal escape (httpx collapses `../` segments, which could otherwise reach
an admin endpoint past the role gate), camera ids containing `.`/`..` path
segments are rejected outright.

## Non-goals

- No public internet exposure outside Tailscale.
- No arbitrary shell execution.
- No arbitrary HTTP proxy or arbitrary endpoint caller.
- No automatic fleetwide destructive action without an exact confirmation string.
- No cloud-model dependency inside TailCam MCP.
