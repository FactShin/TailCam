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
| `default_fps` | `15` | Stream/record frame rate for every camera without its own override. |
| `jpeg_quality` | `80` | MJPEG quality (1–100). |
| `max_width` | `1280` | Max stream width (downscaled if larger; `0` = native). |

These are the **global** streaming defaults (Settings → Streaming). A camera can
override any of them on its page; viewers can only request *lower* values.

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
| `media_dir` | `""` | Folder for recordings, snapshots, thumbnails **and timelapses**. Blank = `<data-dir>/media`. Set to an external drive/NAS path — pick it with the folder browser in Settings → Recording & storage. |
| `node` | `""` | **Storage node**: record and timelapse this node's cameras *on another TailCam node* (peer key, hostname, or base URL). That node pulls the camera stream over the tailnet and writes the files to its own disk. Blank = save here. Falls back to local if the node is unreachable when a capture starts. See [Fleet](fleet#storage-node). |

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

## `[detection]`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` (`false` on low-power hosts) | Global object-detection switch. Each camera can override it. |
| `engine` | `"auto"` | `auto` / `ultralytics` / `opencv`. |
| `model` | `""` | Ultralytics model name/path override. |
| `confidence` | `0.45` | Minimum box confidence. |
| `classes` | `[]` | Only report these labels (empty = all 80 COCO classes). |
| `overlay_default` | `true` | Camera pages start with the box overlay on. |
| `node` | `""` | **Detection node**: run detection and motion labeling on another TailCam node (peer key / hostname / URL). This node then never loads a model — frames are sent to the peer's `POST /api/detect-image`. |

## Low-power hosts

TailCam probes the host at startup (`/api/system` reports `host_model`,
`ram_gb`, `cpu_count`, `low_power`). A **Raspberry Pi** or any machine with
**under 2 GB of RAM** gets the low-power profile:

| Setting | Standard | Low-power |
| --- | --- | --- |
| `stream.default_fps` | 15 | 10 |
| `stream.jpeg_quality` | 80 | 70 |
| `stream.max_width` | 1280 | 960 |
| `motion.sample_fps` | 5 | 3 |
| `detection.enabled` | true | **false** (route it to a bigger node instead) |

It is applied to a fresh install, and once (config version 3) to an existing
file — only for values still at their stock defaults, so anything you tuned
stays. Force it either way with `TAILCAM_LOW_POWER=1|0` in the service
environment. Recordings and HomeKit use the `ultrafast` x264 preset on these
hosts, and the systemd unit caps malloc arenas / OpenCV threads.
