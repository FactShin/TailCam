# Fleet (multi-node)

Run TailCam on several machines and they form a **fleet**: every node's cameras
appear in one dashboard, and node health/admin is available across the tailnet.
There's no central server — nodes discover each other as peers.

## Discovery

With `peers.auto_discover = true` (the default), each node probes online Tailscale
peers and asks `GET /api/system` to identify other TailCam nodes. Discovered peers
are cached briefly and refreshed automatically.

You can also pin peers explicitly with `peers.static` — a list of base URLs, e.g.:

```toml
[peers]
auto_discover = true
static = ["https://garage-pi.your-tailnet.ts.net:8443"]
```

`GET /api/hosts` lists the local node and all peers, each with a `node_key`
(`local` for this node, a short key for peers), `host`, `version`, online status,
and camera count.

Updated peers also report persistent `node_id`, friendly `node_name`, and active
`node_roles`. Older peers remain usable with role metadata shown as unknown.
Renaming a node does not change its UUID; existing host keys and proxy links are
preserved. Choose workloads in [Node configuration](configuration).

## Aggregated cameras

Set a camera list's `scope` to `all` (the default) and TailCam merges local
cameras with every peer's cameras. Each remote camera carries its owning `host`
and a `proxy_prefix` so the dashboard can stream it **through** the owning node —
you never need a direct route to a peer's camera, just to the peer.

Scope `local` shows only this node's cameras. The same `scope` parameter — and
the same merge — applies to `/api/media`, `/api/events`, and `/api/timelapse`,
so a timelapse or recording running on any node shows up (and can be stopped)
from any dashboard.

## Storage node

Every node can send its own cameras' **recordings, motion clips, and
timelapses** to a different node — the box with the big disk, or the one with
the CPU to encode. Choose it in **Settings → Recording & storage → Save this
device's recordings & timelapses on** (each node's free space is shown), or
set `[storage] node`.

How it works:

1. When a capture starts here, this node asks the storage node
   (`POST /api/remote/<this-node>/cameras/<id>/recording/start` or
   `.../timelapse/start`).
2. The storage node **pulls this camera's MJPEG stream over the tailnet** into
   a normal frame buffer and runs its own recorder / timelapse worker against
   it. Files, thumbnails, and database rows live on the storage node; each row
   carries `source_host` (the camera's node) so the gallery attributes it
   correctly while serving it through `/proxy/<storage-node>/...`.
3. Pulled feeds close themselves after ~30 s with nobody reading.

The storage node only ever pulls from **discovered tailnet peers** — never an
arbitrary URL. Connectivity fallback can run locally only when the source has
its storage role enabled. A destination's disabled-role refusal stops the
request without fallback. The picker disables destinations that report storage
off; older peers with unknown roles are still selectable. Snapshots stay local
and require local capture and storage roles. The storage node's own save folder can be browsed and set from here
(the *folder…* link next to the node).

The same idea exists for AI: a [detection node](ai-analysis) runs the models
for a node that shouldn't.

### When the source or the storage node disappears

- The storage node reconnects a stalled pull (no bytes for 15 s) and, if the
  source stops delivering frames for **3 minutes**, finalizes the recording so a
  dead camera node can't leave a clip "recording" forever.
- The source node keeps retrying a stop the storage node didn't acknowledge, and
  if it restarts while the storage node is still recording one of its cameras it
  adopts that session (a `409 already recording` answer) instead of starting a
  second, local clip.
- Timelapse starts return remote rejections, including `4xx` and `5xx`, without
  creating local captures. Uncertain timeouts require checking the storage node
  before retrying. Only an unresolved destination or failed connection permits
  the existing local fallback, and only with local storage enabled. Both capture
  paths preserve a destination's structured disabled-role refusal without fallback.
- Actions proxied to a peer (record, stop a timelapse) no longer forward the
  browser's `Origin`, so they work when the dashboard was opened by IP address
  as well as by MagicDNS name.

## The reverse proxy

Cross-node streaming and media use a constrained reverse proxy at
`/proxy/<node_key>/...`. It forwards ordinary camera/media API paths to the named peer.
For security it **strips inbound `tailscale-*` identity headers** and **refuses to
proxy the `/api/v1/node`, `/api/v1/fleet`, `/mcp`, and nested `/proxy` paths**.
Ambiguous/encoded traversal paths are rejected before contacting a peer. Management is
never tunneled through the generic proxy. See [Security](security).

Motion events include `recording_host` and `recording_proxy_prefix` separately
from the event's own host. **View clip** opens that exact recording, so two nodes
with recording ID 1 cannot be confused. Unresolved owners show unavailable;
legacy events without an owner still refer to their event node.

## Node & fleet management API

TailCam exposes a versioned management API:

- `GET /api/v1/node/health` — full health snapshot (cameras, Tailscale, AI,
  update status, issues).
- `GET /api/v1/node/capabilities` — what the node supports + the caller's
  principal/roles, active workload roles, and optional task readiness.
- `GET /api/v1/node/audit` — audit log (admin only).
- `GET/PATCH /api/v1/node/config` — read or save node name and workload roles
  (saving requires admin); role changes require a server restart. The UUID is read-only.
- `POST /api/v1/node/actions/reload` — restart workers and rediscover (admin).

The fleet equivalents address any node by key and relay to it:

- `GET /api/v1/fleet/nodes/<node_key>/health`
- `GET /api/v1/fleet/nodes/<node_key>/capabilities`
- `GET /api/v1/fleet/nodes/<node_key>/audit`
- `POST /api/v1/fleet/nodes/<node_key>/actions/reload`

Fleet relay is an **explicit allowlist**, not arbitrary proxying — only these
endpoints relay, and the caller's role is re-checked on both the origin and the
target node.

## Fleet workflows from an agent

The [MCP](mcp-overview) server turns the fleet into an agent-ready control plane:

- `list_fleet_nodes`, `get_node_health`, `summarize_fleet_health` — overview.
- `find_offline_cameras` — offline/degraded cameras grouped by node.
- `check_fleet_version_drift` — nodes lagging the newest version.
- `prepare_fleet_admin_plan` — a non-mutating plan with the exact confirm strings.
- `reload_node` / `reload_fleet_nodes` — admin-gated, audited, confirm-string-gated.

See [MCP tools](mcp-tools).

## Versions across the fleet

Keep nodes on the same TailCam version where you can. `check_fleet_version_drift`
flags laggards, and the dashboard shows an update banner when a newer release is
available. Update a node with `tailcam update`.

### Readiness across the fleet

**Settings → Runtime readiness** selects a local node or discovered peer. Each
node reports its own active roles and resource observations. Older peers remain
usable and show readiness as not reported. A failed refresh retains the previous
snapshot with a stale warning; successful checks on another device do not replace
it. Task readiness is diagnostic and does not authorize, reserve, or route work.

Add `?probe=true` to either capabilities endpoint for a bounded Ollama inventory
check on that node. Without it, capability polling does not contact a model server.
Management relays preserve upstream failure status but return generic errors so
peer endpoint credentials and proxy error pages cannot leak into the dashboard.
