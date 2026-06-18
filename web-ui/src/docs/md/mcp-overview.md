# MCP overview

TailCam ships a built-in **Model Context Protocol (MCP)** server that turns a node
and its [fleet](fleet) into an agent-ready control plane for **Codex**,
**Claude**, **Hermes**, and **OpenClaw**. Agents get safe, typed tools, resources,
and prompts for cameras, events, media, health, AI, and administration.

It's a thin layer **over** TailCam's own APIs — it never bypasses them and never
offers arbitrary proxying or shell access.

## Architecture

One transport-agnostic core speaks MCP (revision `2025-06-18`) over JSON-RPC and
wraps TailCam's REST + management APIs. Two transports sit in front of it:

| Mode | How | Use for |
| --- | --- | --- |
| **Local stdio** | `tailcam mcp stdio` | Local Codex, Claude Desktop, local Hermes/OpenClaw. |
| **Streamable HTTP** | `/mcp` over Tailscale | Remote agents and the Claude API connector. |

Both expose the same capabilities:

- **Tools (47)** — read status, control cameras, manage AI/Ollama, drive
  training, fleet admin, and higher-level incident workflows.
- **Resources (10)** — read-only context like `tailcam://fleet`,
  `tailcam://cameras`, `tailcam://system`.
- **Prompts (6)** — reusable workflows (fleet triage, motion investigation, …).

## What agents can do

- Inspect the whole fleet, find offline cameras, summarize health.
- Capture snapshots, start/stop recording, tune motion, restart feeds.
- Stand up local [AI](ai-analysis): list/pull/load Ollama models, enable analysis.
- Configure and run [training](training) sessions, then activate models.
- Reload nodes across the fleet — gated by confirmation strings and audited.

See the full catalog in [MCP tools](mcp-tools).

## Security at a glance

- Local stdio follows the personal-computer trust model (local admin).
- Remote `/mcp` is **fail-closed**: unverified callers are rejected, tools are
  filtered by Tailscale role, fleet/destructive actions need explicit confirm
  strings, and every write is audited.

Full details in [MCP security](mcp-security).

## Turn it on

The CLI `tailcam mcp stdio` works whenever MCP is enabled (`mcp.enabled = true`,
the default). The network endpoint is opt-in:

```toml
[mcp]
enabled = true
http_enabled = true
```

Next: [Connecting agents](mcp-connect).
