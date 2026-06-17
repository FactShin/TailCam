# TailCam MCP Integration Design

## Status

Approved direction: dual-mode TailCam MCP with both local stdio and Tailscale-served
Streamable HTTP. This spec defines the integration before implementation begins.

## References

- MCP specification 2025-06-18: https://modelcontextprotocol.io/specification/2025-06-18
- Official MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Codex MCP configuration: https://developers.openai.com/codex/mcp
- Claude Desktop local MCP extensions: https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop
- Claude API MCP connector: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector

## Product Goal

TailCam MCP turns a TailCam node and its fleet into an agent-ready control plane
for Codex, Claude, and local agent systems such as OpenClaw/Hermes. The MCP
server should expose TailCam's cameras, events, media, health, AI status, and
administration workflows through safe, typed tools, resources, and prompts. It
must feel like a first-class TailCam interface, not a thin collection of raw HTTP
wrappers.

## Core Principles

1. Tailscale remains the network and identity backbone.
2. MCP wraps existing TailCam APIs rather than bypassing them.
3. Read operations are easy; write operations are explicit, audited, and bounded.
4. Local and remote agent access share one implementation core.
5. Fleet actions are powerful but never generic arbitrary proxying.
6. Tool descriptions and server instructions guide agents toward good workflows.
7. Codex, Claude, and Hermes/OpenClaw get documented setup examples on day one.

## Architecture

TailCam gets a new `tailcam.mcp` package with a shared implementation core and
two transports.

```text
Codex / Claude / Hermes
        |
        | stdio: tailcam mcp stdio
        v
TailCam MCP server core
        |
        | HTTP client, local context, or in-process app context
        v
TailCam REST and management APIs

Remote agent
        |
        | Streamable HTTP over Tailscale Serve
        v
TailCam /mcp mount
        |
        v
same TailCam MCP server core
```

The shared core owns tool definitions, resource handlers, prompt templates,
input validation, result shaping, confirmation rules, and error normalization.
The transports only provide connection details and principal context.

## Transports

### Local stdio

Command:

```bash
tailcam mcp stdio
```

The stdio server connects to a running TailCam node at
`TAILCAM_URL` or `http://127.0.0.1:8088` by default. This mode is for local
Codex, Claude Desktop, Claude Code-style clients, and local Hermes/OpenClaw
agents. It does not require TailCam to expose MCP on the network.

### Streamable HTTP

Endpoint:

```text
/mcp
```

TailCam mounts an MCP Streamable HTTP app inside the FastAPI/Starlette server.
When TailCam is served through Tailscale, agents can connect to:

```text
https://<tailcam-host>.<tailnet>.ts.net:<serve-port>/mcp
```

This mode is for remote agents, Claude API MCP connector flows, and any future
hosted TailCam automation. It must use the same Tailscale identity and TailCam
role checks as the v1 management API.

## Configuration

Add an MCP config section:

```toml
[mcp]
enabled = true
http_enabled = true
instructions_profile = "personal"
max_events = 100
max_media = 100
allow_image_content = true
require_confirm_for_writes = true
require_confirm_for_fleet_writes = true
```

`stdio` remains available through the CLI even when `http_enabled` is false,
because local clients can launch it on demand. The HTTP endpoint is mounted only
when both `enabled` and `http_enabled` are true.

## Security And Authorization

### Local stdio

Local stdio follows TailCam's existing personal-computer trust model: if a user
can launch `tailcam mcp stdio`, they can connect to the local TailCam node. MCP
clients still present tool approvals to the user, but TailCam must also enforce
its own write confirmations for dangerous tools.

### Remote HTTP

Remote HTTP MCP must be fail-closed:

- Tailscale Serve identity headers are parsed with TailCam's existing principal
  parser.
- Read tools require a verified local or Tailscale principal.
- Admin tools require TailCam `admin` role.
- Fleet write tools require `admin` plus an explicit confirmation string.
- Every management action records an audit event.
- TailCam never exposes a generic "call arbitrary URL" or arbitrary management
  proxy tool.

### Confirmation Rules

State-changing tools accept `confirm` or `confirm_scope` only where needed.
Examples:

- `capture_snapshot` does not need confirmation.
- `start_recording` and `stop_recording` require `confirm=true` when targeting
  more than one camera.
- `reload_node` requires `confirm_scope="reload:<node_key>"`.
- `reload_fleet_nodes` requires `confirm_scope="reload:fleet:<count>"`.
- Future delete, retention purge, reboot, shutdown, or config rollback tools
  must require exact confirmation strings and audit records.

## Server Instructions

The MCP server initialization instructions should fit the most important rules
in the first 512 characters:

```text
TailCam controls cameras over Tailscale. Prefer read tools before write tools.
Use resources for status/context, tools for actions, and prompts for workflows.
Never attempt arbitrary proxying. Fleet or destructive actions require explicit
confirmation strings and are audited. Summarize risks before changing recording,
motion, AI, retention, or fleet state.
```

The full instructions add examples for triage, incident investigation, and safe
fleet administration.

## Resources

Resources are side-effect free and optimized for agent context.

- `tailcam://system`
  - Current node version, host, local URL, access URL, Tailscale state, media
    bytes, hidden camera count.
- `tailcam://fleet`
  - Local and peer node list with online status, versions, camera counts, and
    proxy prefixes.
- `tailcam://nodes/{node_key}/health`
  - v1 node health for one node.
- `tailcam://nodes/{node_key}/capabilities`
  - v1 node capabilities and caller principal.
- `tailcam://cameras`
  - Aggregated camera list.
- `tailcam://cameras/{camera_id}`
  - One camera's status, settings, transform, motion state, and recording state.
- `tailcam://events/recent`
  - Recent motion events with labels, confidence, descriptions, thumbnails, and
    owner node.
- `tailcam://media/recent`
  - Recent snapshots and recordings with file metadata and owner node.
- `tailcam://audit/recent`
  - Recent management audit events for admin principals.
- `tailcam://ai/status`
  - Ollama/model/training/collection status.

Resources should support small query arguments where the MCP SDK allows resource
templates, such as `limit`, `scope`, `node_key`, and `camera_id`.

## Tools

Tool names use stable lower snake case. Results are concise JSON with human
summaries and machine fields.

### Read Tools

- `get_system_status`
  - Returns `SystemInfo` plus a short health summary.
- `list_fleet_nodes`
  - Returns host/node key/version/reachability/camera count for all known nodes.
- `get_node_health`
  - Inputs: `node_key`.
  - Returns v1 health and issue list.
- `list_cameras`
  - Inputs: `scope` (`all` or `local`), optional `node_key`.
- `inspect_camera`
  - Inputs: `camera_id`.
  - Returns camera state, stream hints, transform, motion, errors.
- `list_recent_events`
  - Inputs: `limit`, optional `camera_id`, optional `scope`.
- `list_recent_media`
  - Inputs: `limit`, optional `camera_id`, optional `media_type`.
- `get_audit_log`
  - Inputs: `limit`, `offset`, optional `node_key`.
  - Admin only.
- `get_ai_status`
  - Returns AI and training state.
- `run_tailcam_doctor`
  - Runs the existing diagnostic checks through a safe subprocess helper or
    shared diagnostic service and returns structured findings.

### Camera Action Tools

- `capture_snapshot`
  - Inputs: `camera_id`.
  - Returns media id, thumbnail/file URLs, and owner node.
- `start_recording`
  - Inputs: `camera_id`, optional `confirm`.
- `stop_recording`
  - Inputs: `camera_id`.
- `set_motion_detection`
  - Inputs: `camera_id`, `enabled`, optional sensitivity fields.
- `update_camera_settings`
  - Inputs: `camera_id`, optional `name`, `properties`, `transform`.
- `restart_camera`
  - Inputs: `camera_id`, `confirm=true`.

### Node And Fleet Action Tools

- `reload_node`
  - Inputs: `node_key`, `confirm_scope`.
- `reload_fleet_nodes`
  - Inputs: `node_keys`, `confirm_scope`, `continue_on_error`.
- `check_fleet_version_drift`
  - Returns nodes with version drift and update availability.
- `prepare_fleet_admin_plan`
  - Inputs: goal text and optional node filter.
  - Returns a non-mutating plan with required confirmations.

### AI And Training Tools

- `set_ai_config`
  - Inputs: `enabled`, `model`, `base_url`, `confirm=true`.
- `test_ai_connection`
  - Returns reachability and model presence.
- `set_training_collection`
  - Inputs: collection enabled, interval, auto-label, dataset id.
- `list_training_datasets`
  - Returns dataset summary.
- `import_events_to_dataset`
  - Inputs: dataset id, optional label filter, `confirm=true`.

### Incident And Workflow Tools

- `summarize_fleet_health`
  - Combines fleet nodes, node health, camera states, recent issues, and version
    drift into a prioritized summary.
- `find_offline_cameras`
  - Returns offline/degraded cameras grouped by node with likely causes.
- `investigate_motion_event`
  - Inputs: event id, optional node key.
  - Returns event details, nearby events, camera state, recording/media links,
    and suggested follow-up.
- `prepare_incident_report`
  - Inputs: time window, optional camera/node filters.
  - Returns markdown suitable for a note, issue, or handoff.
- `suggest_retention_cleanup`
  - Non-mutating analysis of media usage and cleanup options.

## Prompts

Prompts are reusable workflows for clients that expose MCP prompts.

- `tailcam_fleet_triage`
  - Guide an agent through fleet health review and safe next actions.
- `tailcam_motion_investigation`
  - Investigate a motion event, summarize evidence, and suggest next steps.
- `tailcam_camera_tuning`
  - Tune motion, resolution, FPS, transform, and recording settings for a goal.
- `tailcam_tailscale_debug`
  - Diagnose Tailscale Serve, app capabilities, access URLs, and peer discovery.
- `tailcam_ai_setup`
  - Configure local Ollama/model analysis and explain fleet analyzer choices.
- `tailcam_admin_change_plan`
  - Draft a safe change plan before any fleetwide action.

## Image And Media Handling

Agents need visual access, but raw video should not flood context.

- Camera streams are exposed as URLs, not streamed through MCP.
- `capture_snapshot` can return a URL plus optional MCP image content when
  `allow_image_content` is true.
- Recent events expose thumbnail URLs and optional image content for one event at
  a time.
- Recordings return metadata and file URLs. They are not embedded in MCP results.
- Tool outputs include clear size and privacy notes when media is returned.

## Error Handling

All tools normalize errors into structured MCP-friendly responses:

```json
{
  "ok": false,
  "error": {
    "code": "tailcam.peer_unreachable",
    "message": "peer-node is unreachable",
    "retryable": true,
    "status_code": 502
  }
}
```

Recommended error codes:

- `tailcam.not_running`
- `tailcam.unauthorized`
- `tailcam.admin_required`
- `tailcam.confirmation_required`
- `tailcam.node_unknown`
- `tailcam.camera_unknown`
- `tailcam.camera_unavailable`
- `tailcam.peer_unreachable`
- `tailcam.invalid_response`
- `tailcam.timeout`
- `tailcam.unsupported_transport`

## Observability And Audit

The MCP layer adds agent-aware audit metadata for every state-changing action:

- MCP transport: `stdio` or `streamable_http`.
- MCP client name when available.
- Tool name and target node/camera.
- Confirmation string used, without secrets.
- Principal actor/source/roles for HTTP.
- Result and normalized error code.

Read-only tools should not spam the audit log, but the MCP server should log
debug traces when TailCam runs with debug logging enabled.

## Client Setup Targets

### Codex

Project or user `config.toml` example:

```toml
[mcp_servers.tailcam]
command = "tailcam"
args = ["mcp", "stdio"]
env = { TAILCAM_URL = "http://127.0.0.1:8088" }
default_tools_approval_mode = "prompt"
tool_timeout_sec = 60
```

Remote Tailscale example:

```toml
[mcp_servers.tailcam]
url = "https://tailcam-host.example.ts.net:8443/mcp"
default_tools_approval_mode = "prompt"
tool_timeout_sec = 60
```

### Claude Desktop

The first implementation ships a documented JSON config for local stdio:

```json
{
  "mcpServers": {
    "tailcam": {
      "type": "stdio",
      "command": "tailcam",
      "args": ["mcp", "stdio"],
      "env": {
        "TAILCAM_URL": "http://127.0.0.1:8088"
      }
    }
  }
}
```

After the stdio server, docs, and smoke tests pass, a follow-up branch can
package the same server as an `.mcpb` Claude Desktop extension with sensitive
fields marked in the manifest.

### Claude API

Claude API integrations use the remote `/mcp` URL with the current MCP connector
beta header and an MCP toolset allowlist for write tools.

### OpenClaw/Hermes

OpenClaw/Hermes should be treated as a generic MCP-compatible local or remote
agent host:

- Local: launch `tailcam mcp stdio`.
- Remote: connect to the Tailscale `/mcp` URL.
- Recommended default: enable read and incident tools first, then explicitly
  enable admin tools after the operator confirms the fleet scope.

## Documentation Deliverables

Implementation should add:

- `docs/mcp.md` with setup for Codex, Claude Desktop, Claude API, and
  OpenClaw/Hermes.
- `docs/mcp-security.md` with transport, Tailscale, confirmation, and audit
  guidance.
- `examples/mcp/codex.config.toml`
- `examples/mcp/claude_desktop_config.json`
- `examples/mcp/hermes-openclaw.json`

## Testing Strategy

Tests must be written before implementation for each behavior.

- Unit tests for tool input schemas and confirmation rules.
- Unit tests for REST client path construction and error normalization.
- Unit tests for tool result shaping using fake TailCam API responses.
- FastAPI tests for `/mcp` mount enabled/disabled behavior.
- Authorization tests for HTTP MCP read, admin, and fleet write access.
- CLI tests for `tailcam mcp stdio` command registration and environment
  handling.
- Integration tests using synthetic cameras and isolated TailCam data dirs.
- A smoke script that starts TailCam, connects an MCP client, lists tools, reads
  system/fleet resources, captures a snapshot, and verifies audit metadata.

## Implementation Phases

1. Add MCP dependency, config, and CLI shell.
2. Build a typed TailCam API client used by MCP tools.
3. Implement shared MCP server core with instructions, resources, prompts, and
   read-only tools.
4. Add camera action tools and confirmation logic.
5. Add node/fleet admin tools through existing v1 management APIs.
6. Mount Streamable HTTP `/mcp` with Tailscale principal context and admin
   authorization.
7. Add image/media handling with strict size and opt-in behavior.
8. Add docs and example client configurations.
9. Add CI and smoke verification for stdio plus HTTP MCP.

Each phase should commit independently and keep the server usable after every
commit.

## Non-Goals For The First MCP Branch

- No public internet exposure outside Tailscale.
- No arbitrary shell execution tool.
- No arbitrary HTTP proxy or arbitrary TailCam endpoint caller.
- No automatic fleetwide destructive action without exact confirmation.
- No cloud model dependency inside TailCam MCP.
- No replacement for the desktop app UI. MCP is an agent integration layer.

## Acceptance Criteria

- `tailcam mcp stdio` works with Codex and Claude Desktop local MCP clients.
- TailCam serves `/mcp` over Streamable HTTP when enabled.
- Codex can connect through stdio and remote HTTP examples.
- Claude Desktop can connect through the documented stdio config.
- Generic Hermes/OpenClaw MCP config examples are present.
- Resources expose system, fleet, camera, event, media, audit, and AI context.
- Tools cover read workflows, camera actions, node/fleet admin actions, and
  higher-level incident workflows.
- Remote HTTP tools enforce verified Tailscale principal and admin role for
  privileged actions.
- State-changing tools are audited with MCP metadata.
- Tests cover authorization, confirmation, error normalization, CLI wiring,
  resources, tools, and smoke client behavior.
- Existing web UI, desktop shell, REST APIs, and fleet behavior continue to pass
  their tests.
