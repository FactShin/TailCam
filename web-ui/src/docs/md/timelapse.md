# Timelapse

TailCam's timelapse is built for long captures — 3D prints, construction, plants,
weather. Raw frames are kept on disk so post-processing can later stitch them into
smooth, flowing motion. Manage timelapses on the **Timelapse** screen.

## Capturing

Start a capture on any camera with an interval and (optional) duration:

- **Interval** (`interval_seconds`, default 2.0) — seconds between captured frames.
- **Output fps** (`output_fps`, default 30) — playback rate of the encoded video.
- **Duration** — 0 means run until you stop it.
- **JPEG quality** (default 90) — quality of stored source frames.
- **Max frames** — a safety cap so a forgotten capture can't fill the disk
  (0 = unlimited).

API: `POST /api/cameras/<id>/timelapse/start`. While capturing, a timelapse is in
the `capturing` state and counts frames as they're stored.

### Across the fleet

The Timelapse screen lists captures from **every node** (`GET /api/timelapse`
merges peers; `scope=local` for one node). A capture started on another node's
camera runs on that node and shows up here with its `host` and `proxy_prefix`,
so **Stop & save**, encode, smooth, and delete all work from any dashboard.
Cameras are keyed by host *and* id — two Pis with `/dev/video0` never collide.

With a [storage node](fleet#storage-node) configured, the capture runs on that
node instead: it pulls the camera's stream and keeps the frames and video on its
disk; the row carries `source_host` (the camera's node) and `host` (where it is
stored).

## Encoding

When you stop a capture (or it hits its duration), encode the frames into a video:

```
POST /api/timelapse/<id>/stop
POST /api/timelapse/<id>/encode
```

The encoded MP4 (H.264, browser-playable, via the bundled ffmpeg) is served at
`/timelapse/<id>/file` with a thumbnail at `/timelapse/<id>/thumbnail`.

## Smoothing (frame interpolation)

"Smooth" turns choppy interval footage into flowing motion by interpolating
intermediate frames up to a target fps, and evens out exposure flicker.

| Setting | Default | Meaning |
| --- | --- | --- |
| `auto_smooth` | false | Smooth automatically after encoding. |
| `smooth_target_fps` | 60 | Interpolated playback rate. |
| `smooth_interpolate` | true | Generate in-between frames. |
| `smooth_deflicker` | true | Even out exposure changes. |
| `smooth_quality` | high | `standard` / `high` / `maximum`. |
| `smooth_engine` | ffmpeg | `ffmpeg` (bundled, works everywhere) or `rife`. |

Trigger it with `POST /api/timelapse/<id>/smooth`. The smoothed variant is served
at `/timelapse/<id>/smooth`.

### Engines

- **ffmpeg** — uses `minterpolate`; bundled and CPU-based, works everywhere.
- **rife** — `rife-ncnn-vulkan`, higher quality and GPU-accelerated, but must be
  installed (`rife_path`, `rife_model`). A failed RIFE run **automatically falls
  back to ffmpeg**, so smoothing never hard-fails on a missing GPU.

Check available engines with `GET /api/postprocess`.

## Printer / failure analysis

For 3D-print timelapses, TailCam can analyze frames as they're captured to flag
likely print failures (spaghetti, detachment). Enable with `analysis_enabled` and
set `analysis_cadence_seconds` (default 60s). Results appear as analysis events on
the timelapse (`GET /api/timelapse/<id>/analysis-events`) with a state of
`healthy`, `possible_failure`, `failure`, or `uncertain`. This uses the same
local [AI](ai-analysis) backend.

## Frames and storage

Individual captured frames are addressable at
`/timelapse/<id>/frame/<frame_number>`. Timelapses live under the media
location (`<media_dir>/timelapse/<id>/`), so a custom save folder or external
drive applies to them too; captures made before 1.8 stay where they were.
Timelapse bytes count toward the storage total reported by the system. Delete a
timelapse with `DELETE /api/timelapse/<id>`.
