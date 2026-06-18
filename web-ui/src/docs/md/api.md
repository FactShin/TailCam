# API reference

TailCam serves a REST API under `/api`, media/stream files under `/stream`,
`/media`, `/timelapse`, and `/events`, a versioned management API under
`/api/v1`, and (when enabled) the [MCP](mcp-overview) endpoint at `/mcp`. The same
dashboard you're reading this in is built entirely on these endpoints.

## System

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/system` | Version, host, Tailscale state, URLs, media bytes, hidden count. |
| POST | `/api/system/reload` | Re-scan devices and restart local workers. |
| GET | `/api/update` | Update availability. |
| GET | `/api/hosts` | Local node + peers (with `node_key`). |

## Cameras

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/cameras?scope=all\|local` | List cameras. |
| GET | `/api/cameras/{id}` | One camera. |
| PATCH | `/api/cameras/{id}` | Update name/properties/transform/motion. |
| POST | `/api/cameras/{id}/restart` | Recover a stuck feed. |
| DELETE | `/api/cameras/{id}` | Hide from discovery. |
| POST | `/api/cameras/refresh` | Discover + restart all. |
| POST | `/api/cameras/restore-hidden` | Un-hide and re-scan. |
| POST | `/api/cameras/{id}/snapshot` | Capture a still. |
| POST | `/api/cameras/{id}/recording/start` | Start recording. |
| POST | `/api/cameras/{id}/recording/stop` | Stop recording. |
| POST | `/api/cameras/{id}/detect` | Run the active detection model on a frame. |

## Streams & files

| Path | Returns |
| --- | --- |
| `/stream/{id}.mjpg` | Live MJPEG stream. |
| `/stream/{id}/snapshot.jpg` | Latest frame as JPEG. |
| `/media/{id}/file` | Recording/snapshot file. |
| `/media/{id}/thumbnail` | Media thumbnail. |
| `/events/{id}/thumbnail` | Motion-event thumbnail. |
| `/timelapse/{id}/file` | Encoded timelapse video. |
| `/timelapse/{id}/smooth` | Smoothed variant. |

## Events & media

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/events` | Motion events (`camera_id`, `limit`, `offset`, `scope`). |
| GET | `/api/media` | Media (`camera_id`, `media_type`, `limit`, `offset`, `scope`). |
| DELETE | `/api/media/{id}` | Delete media. |

## Timelapse

`GET/POST /api/timelapse...` — start, stop, encode, smooth, list, delete, and
read analysis events. See [Timelapse](timelapse).

## AI

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/ai` | Status (enabled, reachable, model, model_present). |
| POST | `/api/ai` | Set enabled/model/base_url. |
| GET | `/api/ai/models` | Installed Ollama models + reachability. |
| POST | `/api/ai/pull` | Download an Ollama model. |
| POST | `/api/ai/load` | Warm a model into memory. |

## Training, datasets & models

`GET/POST /api/training`, `/api/training/collection`, `/api/datasets...`,
`/api/samples...`, `/api/models...`, `/api/training/runs...`. See
[Training](training).

## Versioned management (`/api/v1`)

Node and fleet management with [role](security) enforcement and audit:

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/node/health` | Health snapshot. |
| GET | `/api/v1/node/capabilities` | Capabilities + caller principal. |
| GET | `/api/v1/node/audit` | Audit log (admin). |
| POST | `/api/v1/node/actions/reload` | Reload node (admin). |
| * | `/api/v1/fleet/nodes/{node_key}/...` | Same, relayed to any node. |

See [Fleet](fleet) for the relay model.

## MCP

`POST /mcp` — Streamable HTTP MCP endpoint (when `mcp.http_enabled`). See
[MCP overview](mcp-overview).

## Interactive API explorer

TailCam serves the live OpenAPI schema at `/openapi.json`, with interactive
explorers at **`/api-docs`** (Swagger UI) and **`/api-redoc`** (ReDoc). (The
`/docs` path hosts this wiki, so the API docs live under `/api-docs`.)

> The dashboard talks to these endpoints over loopback/the tailnet. There's no
> separate API key — access is governed by Tailscale identity and the
> [role model](security).
