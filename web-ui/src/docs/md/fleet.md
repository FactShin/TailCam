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

## Aggregated cameras

Set a camera list's `scope` to `all` (the default) and TailCam merges local
cameras with every peer's cameras. Each remote camera carries its owning `host`
and a `proxy_prefix` so the dashboard can stream it **through** the owning node —
you never need a direct route to a peer's camera, just to the peer.

Scope `local` shows only this node's cameras.

## The reverse proxy

Cross-node streaming and media use a constrained reverse proxy at
`/proxy/<node_key>/...`. It forwards only safe view/media paths to the named peer.
For security it **strips inbound `tailscale-*` identity headers** and **refuses to
proxy the `/api/v1/node` and `/api/v1/fleet` management paths** — management is
never tunneled through the generic proxy. See [Security](security).

## Node & fleet management API

TailCam exposes a versioned management API:

- `GET /api/v1/node/health` — full health snapshot (cameras, Tailscale, AI,
  update status, issues).
- `GET /api/v1/node/capabilities` — what the node supports + the caller's
  principal/roles.
- `GET /api/v1/node/audit` — audit log (admin only).
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
