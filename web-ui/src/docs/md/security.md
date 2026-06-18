# Security & access

TailCam's security model rests on three ideas: **Tailscale is the perimeter**,
**identity is verified only where it can be trusted**, and **privileged actions
are explicit and audited**.

## The perimeter

TailCam is meant to run on your tailnet, not the public internet. Tailscale
encrypts traffic and ensures only your devices can reach a node. TailCam does not
enable Funnel, and the [MCP](mcp-security) HTTP endpoint is off by default and
restricted to Tailscale-served access.

## Principals and roles

When a request arrives, TailCam derives a **principal**: who is calling, from
where, and with what roles. Roles are hierarchical:

| Role | Can do |
| --- | --- |
| `viewer` | View cameras, streams, events, media. |
| `operator` | + control cameras (snapshot, record, motion, settings, restart). |
| `admin` | + node/fleet reloads, AI/training/model changes, audit log. |

A principal also records its `source`: `local`, `tailscale-user`,
`tailscale-node`, or `unverified`.

## Where identity is trusted

Tailscale Serve terminates TLS and forwards identity headers (the user's login,
display name, and app-capability grants) to TailCam **over loopback**. TailCam
therefore trusts those identity headers **only when the request comes from
loopback** (`127.0.0.1` / `::1`). A request from anywhere else is `unverified`
with no roles.

To keep that loopback anchor honest, TailCam runs its web server with
`proxy_headers=false`, so a forwarded `X-Forwarded-For` can never make a remote
request look like loopback.

- **Local requests** (localhost) are treated as a trusted local admin — the
  personal-computer model.
- **Tailscale users** get roles from your tailnet ACL **grants** (app
  capabilities). 
- **Everything else** is unverified and denied privileged actions.

## App capability grants

TailCam advertises a Tailscale app capability so your ACL can map tailnet users
and nodes to TailCam roles. A grant looks like:

```json
{
  "app": {
    "factshin.github.io/cap/tailcam": [
      { "roles": ["admin"] }
    ]
  }
}
```

Fleet relay between nodes also relies on app capabilities so a peer can present a
verifiable role when it relays a management call. The node-to-node serve command
uses `--accept-app-caps` when supported.

## Audit log

Every state-changing management action is recorded: actor, source, action,
target, result, and metadata. View it with `GET /api/v1/node/audit` (admin) or, in
the dashboard, via the node's audit view. [MCP](mcp-security) writes are audited
too, tagged with the transport (`stdio` / `streamable_http`) and client name.

## What TailCam never does

- No arbitrary HTTP proxy or "call any URL" capability.
- No arbitrary shell execution.
- No public-internet exposure by default.
- No management actions tunneled through the generic camera proxy — the proxy
  strips `tailscale-*` headers and refuses the `/api/v1/node` and `/api/v1/fleet`
  paths.

## MCP specifics

The agent-facing [MCP](mcp-overview) layer enforces the same role model, is
fail-closed over HTTP (unverified callers get 401), filters tools by role, and
requires explicit confirmation strings for destructive and fleet actions. See
[MCP security](mcp-security) for the full model.
