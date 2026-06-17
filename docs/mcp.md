# TailCam MCP

TailCam ships a Model Context Protocol (MCP) server that turns a node and its
fleet into an agent-ready control plane for **Codex**, **Claude**, and local
agent systems such as **OpenClaw/Hermes**. The server exposes TailCam's cameras,
events, media, health, AI status, and administration workflows as safe, typed
tools, resources, and prompts.

It wraps TailCam's existing REST and v1 management APIs — it never bypasses them,
and it never offers arbitrary proxying or shell execution. See
[`mcp-security.md`](mcp-security.md) for the trust and audit model.

## Two ways to connect

| Mode | Command / URL | Use it for |
| --- | --- | --- |
| **Local stdio** | `tailcam mcp stdio` | Local Codex, Claude Desktop, Claude Code, and local Hermes/OpenClaw agents. |
| **Streamable HTTP** | `https://<host>.<tailnet>.ts.net:<serve-port>/mcp` | Remote agents and the Claude API MCP connector, over Tailscale. |

Both share one implementation core, so tools, resources, and prompts behave
identically regardless of transport.

### Local stdio

`tailcam mcp stdio` speaks MCP on stdin/stdout and connects to a running TailCam
node at `$TAILCAM_URL` (default `http://127.0.0.1:8088`). Start TailCam first
(`tailcam run` or the background service), then point your MCP client at the
command. Local stdio follows TailCam's personal-computer trust model: whoever can
launch the process acts as the local admin.

### Streamable HTTP

The `/mcp` endpoint is mounted inside the TailCam web server when **both**
`[mcp] enabled` and `[mcp] http_enabled` are true:

```toml
[mcp]
enabled = true
http_enabled = true
```

When TailCam is served through Tailscale, remote agents connect to
`https://<host>.<tailnet>.ts.net:<serve-port>/mcp`. Remote access is fail-closed
and uses the same Tailscale identity and role checks as the v1 management API.

## Configuration

```toml
[mcp]
enabled = true                       # master switch (stdio + HTTP)
http_enabled = false                 # also mount the network /mcp endpoint
instructions_profile = "personal"    # personal | fleet
max_events = 100                     # cap for event/recent reads
max_media = 100                      # cap for media/recent reads
allow_image_content = true           # permit opt-in image content in results
require_confirm_for_writes = true    # confirm restart/AI/import writes
require_confirm_for_fleet_writes = true  # confirm reload_node / reload_fleet_nodes
```

`tailcam mcp stdio` works even when `http_enabled` is false; local clients launch
it on demand.

## What the server exposes

### Tools

Read tools (`get_system_status`, `list_fleet_nodes`, `get_node_health`,
`list_cameras`, `inspect_camera`, `list_recent_events`, `list_recent_media`,
`get_ai_status`, `get_audit_log`), camera actions (`capture_snapshot`,
`start_recording`, `stop_recording`, `set_motion_detection`,
`update_camera_settings`, `restart_camera`), node/fleet actions (`reload_node`,
`reload_fleet_nodes`, `check_fleet_version_drift`, `prepare_fleet_admin_plan`),
AI/training tools (`set_ai_config`, `test_ai_connection`,
`set_training_collection`, `list_training_datasets`, `import_events_to_dataset`),
and higher-level workflows (`summarize_fleet_health`, `find_offline_cameras`,
`investigate_motion_event`, `prepare_incident_report`,
`suggest_retention_cleanup`).

Tools are filtered by the caller's role: viewers see read tools, operators add
camera actions, admins see everything.

### Resources

`tailcam://system`, `tailcam://fleet`, `tailcam://cameras`,
`tailcam://cameras/{camera_id}`, `tailcam://nodes/{node_key}/health`,
`tailcam://nodes/{node_key}/capabilities`, `tailcam://events/recent`,
`tailcam://media/recent`, `tailcam://audit/recent` (admin), `tailcam://ai/status`.

### Prompts

`tailcam_fleet_triage`, `tailcam_motion_investigation`, `tailcam_camera_tuning`,
`tailcam_tailscale_debug`, `tailcam_ai_setup`, `tailcam_admin_change_plan`.

## Client setup

### Codex

Local stdio (`~/.codex/config.toml` or project `config.toml`), see
[`examples/mcp/codex.config.toml`](../examples/mcp/codex.config.toml):

```toml
[mcp_servers.tailcam]
command = "tailcam"
args = ["mcp", "stdio"]
env = { TAILCAM_URL = "http://127.0.0.1:8088" }
default_tools_approval_mode = "prompt"
tool_timeout_sec = 60
```

Remote over Tailscale:

```toml
[mcp_servers.tailcam]
url = "https://tailcam-host.example.ts.net:8443/mcp"
default_tools_approval_mode = "prompt"
tool_timeout_sec = 60
```

### Claude Desktop

See [`examples/mcp/claude_desktop_config.json`](../examples/mcp/claude_desktop_config.json):

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

### Claude API

Use the remote `/mcp` URL with the MCP connector beta and an explicit allowlist
for write tools. Keep read tools open and gate admin/fleet writes behind operator
approval.

### OpenClaw / Hermes

Treat TailCam as a generic MCP host. Locally, launch `tailcam mcp stdio`;
remotely, connect to the Tailscale `/mcp` URL. A good default is to enable read
and incident tools first, then enable admin tools after the operator confirms the
fleet scope. See [`examples/mcp/hermes-openclaw.json`](../examples/mcp/hermes-openclaw.json).

## Quick check

```bash
# Start a node, then verify the command is wired:
tailcam mcp --help
tailcam mcp stdio   # then send an MCP initialize over stdin from your client
```
