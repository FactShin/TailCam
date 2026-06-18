# MCP tools, resources & prompts

The full catalog the [MCP](mcp-overview) server exposes. Tools are filtered by the
caller's [role](security): viewers see read tools, operators add camera actions,
admins see everything. Writes are audited; destructive/fleet actions require a
confirmation argument.

## Read tools (viewer)

| Tool | Purpose |
| --- | --- |
| `get_system_status` | Node version, host, Tailscale state, access URL, media usage. |
| `list_fleet_nodes` | Every known node: key, host, version, reachability, camera count. |
| `get_node_health` | Full health snapshot + issues for one node. |
| `list_cameras` | Cameras across the fleet (or one node). |
| `inspect_camera` | One camera's state, geometry, transform, errors, stream URLs. |
| `list_recent_events` | Recent motion events with AI labels and thumbnail URLs. |
| `list_recent_media` | Recent snapshots/recordings with file/thumbnail URLs. |
| `get_ai_status` | AI reachability + training engine/collection state. |
| `get_audit_log` | Recent audit events (admin only). |

## Camera actions (operator)

| Tool | Notes |
| --- | --- |
| `capture_snapshot` | Returns media id + file URL. |
| `start_recording` / `stop_recording` | Start/stop a clip. |
| `set_motion_detection` | Enable/disable motion per camera. |
| `update_camera_settings` | Name, properties, transform. |
| `restart_camera` | Requires `confirm=true`. |

## AI & Ollama (admin)

| Tool | Notes |
| --- | --- |
| `list_ollama_models` | Installed models + reachability. |
| `pull_ollama_model` | Download a model. `confirm=true`; can take minutes. |
| `load_ollama_model` | Warm/"start" a model into memory. |
| `set_ai_config` | Enable analysis, set model/base URL. `confirm=true`. |
| `test_ai_connection` | Reachability + model presence. |

## Training & datasets (admin)

| Tool | Notes |
| --- | --- |
| `set_training_collection` | Configure auto-collection from cameras. |
| `create_dataset` / `get_dataset` / `delete_dataset` | `delete` needs `confirm=true`. |
| `list_dataset_samples` / `relabel_sample` / `delete_sample` | Manage samples. |
| `import_events_to_dataset` | Add event snapshots. `confirm=true`. |
| `start_training_run` | Fine-tune on a dataset. `confirm=true`. |
| `list_training_runs` / `get_training_run` / `stop_training_run` | Drive runs. |

## Models (admin)

`list_models`, `register_model`, `activate_model`, `deactivate_model`,
`delete_model` (`confirm=true`).

## Node & fleet (admin)

| Tool | Notes |
| --- | --- |
| `reload_node` | Needs `confirm_scope="reload:<node_key>"`. |
| `reload_fleet_nodes` | Needs `confirm_scope="reload:fleet:<count>"`. |
| `check_fleet_version_drift` | Nodes lagging the newest release. |
| `prepare_fleet_admin_plan` | Non-mutating plan with the exact confirm strings. |

## Incident workflows (viewer)

`summarize_fleet_health`, `find_offline_cameras`, `investigate_motion_event`,
`prepare_incident_report`, `suggest_retention_cleanup`.

## Resources

Side-effect-free context, returned as JSON:

`tailcam://system`, `tailcam://fleet`, `tailcam://cameras`,
`tailcam://cameras/{camera_id}`, `tailcam://nodes/{node_key}/health`,
`tailcam://nodes/{node_key}/capabilities`, `tailcam://events/recent`,
`tailcam://media/recent`, `tailcam://audit/recent` (admin), `tailcam://ai/status`.

## Prompts

Reusable workflows: `tailcam_fleet_triage`, `tailcam_motion_investigation`,
`tailcam_camera_tuning`, `tailcam_tailscale_debug`, `tailcam_ai_setup`,
`tailcam_admin_change_plan`.

## Error model

Failures are normalized so agents see a stable code:

```json
{ "ok": false, "error": { "code": "tailcam.peer_unreachable",
  "message": "...", "retryable": true, "status_code": 502 } }
```

Codes include `tailcam.not_running`, `tailcam.unauthorized`,
`tailcam.admin_required`, `tailcam.confirmation_required`, `tailcam.node_unknown`,
`tailcam.camera_unknown`, `tailcam.peer_unreachable`, `tailcam.timeout`, and more.

See [MCP security](mcp-security) for confirmation and audit details.
