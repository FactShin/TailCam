# Configuration reference

App-level settings live in a human-editable **TOML** file. Per-camera display
settings live there too; dynamic data (camera registry, media index, events) lives
in SQLite instead.

## The config file

- Location: TailCam's config directory (override with `TAILCAM_CONFIG_DIR`). Find
  the resolved path with `tailcam doctor`.
- Edit it directly, or use `tailcam config --edit`. After editing the running
  service, `tailcam restart`.
- A malformed file never bricks TailCam: the bad file is backed up to `*.bad` and
  defaults are used until you fix it.

## `[server]`

| Key | Default | Meaning |
| --- | --- | --- |
| `host` | `127.0.0.1` | Bind address. |
| `port` | `8088` | Local HTTP port. |

## `[stream]`

| Key | Default | Meaning |
| --- | --- | --- |
| `default_fps` | `15` | Stream/record frame rate. |
| `jpeg_quality` | `80` | MJPEG quality (1–100). |
| `max_width` | `1280` | Max stream width (downscaled if larger). |

## `[motion]`

See [Motion detection](motion-detection).

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Global motion default. |
| `sensitivity` | `50` | 1–100, higher = more sensitive. |
| `min_area` | `800` | Min changed-pixel area. |
| `sample_fps` | `5` | Motion sampling rate. |
| `cooldown_seconds` | `5.0` | Gap before a new event. |
| `auto_record` | `false` | Record on motion. |
| `record_tail_seconds` | `5.0` | Extra recording after motion ends. |

## `[retention]`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Auto-cleanup master switch (opt-in; nothing is deleted when off). |
| `max_gb` | `10.0` | Total media budget (GB). |
| `max_age_days` | `30` | Delete media older than this. |

## `[storage]`

| Key | Default | Meaning |
| --- | --- | --- |
| `media_dir` | `""` | Folder for recordings/snapshots/thumbnails. Blank = `<data-dir>/media`. Set to an external drive/NAS path. Editable from Settings → Recording & storage. |

## `[tailscale]`

See [Tailscale setup](tailscale).

| Key | Default | Meaning |
| --- | --- | --- |
| `auto_serve` | `true` | Run `tailscale serve` on startup. |
| `serve_port` | `8443` | Tailnet HTTPS port — **443, 8443, or 10000 only**. |

## `[peers]`

See [Fleet](fleet).

| Key | Default | Meaning |
| --- | --- | --- |
| `auto_discover` | `true` | Probe tailnet peers for TailCam nodes. |
| `static` | `[]` | Explicit peer base URLs. |

## `[cameras]`

| Key | Default | Meaning |
| --- | --- | --- |
| `hidden` | `[]` | Camera ids hidden from discovery. |

## `[ai]`

See [AI analysis](ai-analysis).

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Master switch. |
| `base_url` | `http://localhost:11434` | Ollama endpoint. |
| `model` | `moondream` | Vision model. |
| `timeout` | `20.0` | Per-request timeout (s). |
| `prompt` | (built-in) | Constrained JSON instruction. |

## `[timelapse]`

See [Timelapse](timelapse).

| Key | Default | Meaning |
| --- | --- | --- |
| `default_interval_seconds` | `2.0` | Seconds between frames. |
| `default_output_fps` | `30` | Encoded playback rate. |
| `jpeg_quality` | `90` | Source frame quality. |
| `max_frames` | `0` | Frame cap (0 = unlimited). |
| `auto_smooth` | `false` | Smooth after encoding. |
| `smooth_target_fps` | `60` | Interpolated rate. |
| `smooth_interpolate` | `true` | Generate in-between frames. |
| `smooth_deflicker` | `true` | Even out exposure. |
| `smooth_quality` | `high` | `standard`/`high`/`maximum`. |
| `smooth_engine` | `ffmpeg` | `ffmpeg` or `rife`. |
| `rife_path` | `""` | Path to `rife-ncnn-vulkan`. |
| `rife_model` | `rife-v4.6` | RIFE model folder. |
| `analysis_enabled` | `false` | Per-frame failure analysis. |
| `analysis_cadence_seconds` | `60.0` | Analysis interval. |

## `[training]`

See [Training](training).

| Key | Default | Meaning |
| --- | --- | --- |
| `engine` | `ultralytics` | Training engine (auto-detected). |
| `collect_enabled` | `false` | Continuous collection. |
| `collect_interval_seconds` | `30.0` | Seconds between samples. |
| `auto_label` | `true` | Weak-label new samples with Ollama. |
| `active_dataset_id` | `0` | Dataset for collection (0 = none). |
| `classes` | person, animal, … | Class list. |
| `base_model` | `yolo11n-cls.pt` | Classification base. |
| `epochs` | `30` | Training epochs. |
| `image_size` | `224` | Classification input size. |
| `active_model_id` | `0` | Active model (0 = Ollama). |
| `detect_base_model` | `yolo11n.pt` | Detection base. |
| `detect_image_size` | `640` | Detection input size. |
| `detect_conf` | `0.35` | Min live-detector box confidence. |

## `[mcp]`

See [MCP overview](mcp-overview) and [MCP security](mcp-security).

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Master switch (stdio + HTTP). |
| `http_enabled` | `false` | Also mount the network `/mcp` endpoint. |
| `instructions_profile` | `personal` | `personal` or `fleet`. |
| `max_events` | `100` | Cap for event reads. |
| `max_media` | `100` | Cap for media reads. |
| `allow_image_content` | `true` | Permit opt-in image content in results. |
| `require_confirm_for_writes` | `true` | Confirm restart/AI/import writes. |
| `require_confirm_for_fleet_writes` | `true` | Confirm node/fleet reloads. |
