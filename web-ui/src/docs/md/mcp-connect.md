# Connecting agents

> **Fastest path: the MCP page.** Open **MCP** in the nav — it shows this
> node's live MCP URLs and ready-to-paste setup for Claude Code, Codex,
> OpenClaw, Hermes, and Claude Desktop, plus the network-endpoint toggle
> (no restart needed). This page is the reference behind it.

This page shows how to connect Codex, Claude, Hermes, and OpenClaw to TailCam's
[MCP](mcp-overview) server. Example config files also ship in the repo under
`examples/mcp/`.

## Step 0 — run a node

The MCP server talks to a running TailCam node:

```bash
tailcam run        # or: tailcam start  (background service)
```

## Local connection (stdio)

The agent launches `tailcam mcp stdio` itself; it connects to the node at
`TAILCAM_URL` (default `http://127.0.0.1:8088`).

### Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.tailcam]
command = "tailcam"
args = ["mcp", "stdio"]
env = { TAILCAM_URL = "http://127.0.0.1:8088" }
default_tools_approval_mode = "prompt"
tool_timeout_sec = 60
```

### Claude Desktop (Settings → Developer → Edit Config)

```json
{
  "mcpServers": {
    "tailcam": {
      "type": "stdio",
      "command": "tailcam",
      "args": ["mcp", "stdio"],
      "env": { "TAILCAM_URL": "http://127.0.0.1:8088" }
    }
  }
}
```

### Hermes / OpenClaw

They're generic MCP hosts — use the same `command` / `args` / `env`. A good
default is to enable read and incident tools first, then admin tools after you've
confirmed the fleet scope.

Restart the agent and TailCam's tools appear.

## Remote connection (over Tailscale)

Enable the network endpoint in TailCam's config:

```toml
[mcp]
enabled = true
http_enabled = true
```

Point the agent at the served URL (see [Tailscale](tailscale) for the exact host
and port — 443/8443/10000):

```
https://<your-node>.<tailnet>.ts.net:8443/mcp
```

Codex remote example:

```toml
[mcp_servers.tailcam]
url = "https://tailcam-host.example.ts.net:8443/mcp"
```

Remote callers are gated by Tailscale identity and role — admin/fleet tools
require the `admin` role. See [MCP security](mcp-security).

## Verify it works

Ask the agent to call `get_system_status` or read the `tailcam://fleet` resource.

### Worked example — stand up local AI

> "List the Ollama models, pull moondream, load it, enable AI, and confirm it's
> reachable."

This drives `list_ollama_models → pull_ollama_model → load_ollama_model →
set_ai_config → test_ai_connection`. See [AI analysis](ai-analysis).

### Worked example — configure a training session

> "Create a detection dataset named front-door, import recent events into it,
> start a 30-epoch training run, and tell me when it's done."

This drives `create_dataset → import_events_to_dataset → start_training_run →
get_training_run`, then `activate_model` when complete. See [Training](training).

## Roles and approvals

- **stdio** runs as local admin — the agent can use every tool (your MCP client
  still surfaces its own per-tool approvals).
- **remote** is limited to the caller's tailnet role. Give read-only agents a
  `viewer` grant; give automation `admin` only when you intend it.

Full tool catalog: [MCP tools](mcp-tools).
