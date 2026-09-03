# Cameras

The **Cameras** screen is TailCam's home. It shows every camera on this node (and
across the [fleet](fleet) when peers are present), each as a live tile.

## Discovery

TailCam scans the system for capture devices on startup and exposes them
automatically. Supported backends include:

- **v4l2** — Linux (USB webcams, CSI cameras)
- **AVFoundation** — macOS
- **DirectShow / MSMF** — Windows
- **synthetic** — a built-in test source (`TAILCAM_SYNTHETIC=1`)

Click **Refresh** to re-scan after plugging in a device, or run:

```bash
tailcam cameras       # list detected cameras
```

## Viewing

- Click a tile to open the camera detail view: a larger live stream plus controls.
- The stream is **MJPEG** by default, served at `/stream/<camera_id>.mjpg`. A
  single still is at `/stream/<camera_id>/snapshot.jpg`.
- Open the **Video wall** (press `W`) to watch all cameras simultaneously.

### Stream settings are device-wide

Frame rate, JPEG quality, and maximum width are **camera settings saved on the
node that owns the camera** — one value for everyone, on every phone, browser,
and dashboard. There is no per-browser tuning to keep in sync.

- **Global defaults** live in **Settings → Streaming (all cameras)** (`[stream]`
  in config: `default_fps`, `jpeg_quality`, `max_width`).
- **Per-camera overrides** live on the camera page under *Camera settings*
  (stream frame rate / quality / max width). "Use global defaults" clears them.
- Viewers can only go *lower*: the dashboard grid and video wall ask for a
  low-bandwidth stream, and the server caps any request at the camera's
  settings. Zoom and pan remain a per-screen gesture (they don't change the
  camera).

On a Raspberry Pi, 10 fps at 960 px wide is a good balance; the
[low-power profile](configuration#low-power-hosts) sets that automatically.

## Settings

From a camera's detail view (or via the API) you can change:

- **Name** — a friendly label.
- **Properties** — `width`, `height`, `fps`, and `brightness` / `contrast` /
  `saturation` where the device supports them.
- **Transform** — `rotation` (0/90/180/270), `flip_h`, `flip_v`. Useful for
  upside-down or mirrored mounts.
- **Stream** — `fps`, `quality`, `max_width` overrides (see above).
- **Motion detection** — toggle per camera, **remembered across restarts**. See
  [Motion detection](motion-detection).
- **Object detection** — on/off per camera (defaults to the global switch in AI
  Studio). Off means no model runs for this camera and no boxes are polled. See
  [AI analysis](ai-analysis).

Changes are applied live and persisted. The API shape:

```
PATCH /api/cameras/<id>
{ "stream": { "fps": 10, "quality": null },    // null = inherit the global default
  "detection_enabled": false,                   // or "clear_detection_override": true
  "motion_enabled": true }
```

### USB bandwidth on a Raspberry Pi

On Linux, TailCam asks V4L2 webcams for **MJPEG** frames instead of raw YUYV.
Raw 720p is ~27 MB/s per camera, which saturates a Pi's USB 2.0 bus the moment a
second camera is plugged in (the frame rate of *both* collapses); MJPEG is about
a tenth of that. Set `TAILCAM_RAW_V4L2=1` in the service environment to opt out
for a device that misbehaves.

## Restarting a stuck feed

If a camera shows **degraded** or **offline** and you believe the device is fine,
use **Restart** on the camera (or `POST /api/cameras/<id>/restart`). This
re-opens the capture device without restarting TailCam.

`tailcam doctor` and [Troubleshooting](troubleshooting) cover deeper diagnosis.

## Hiding and restoring cameras

Some systems expose phantom devices (e.g. Raspberry Pi ISP/codec nodes). Delete a
camera to **hide** it from discovery — it's added to `cameras.hidden` in config
and skipped on future scans. To bring hidden cameras back, use **Restore hidden**
on the Cameras screen (`POST /api/cameras/restore-hidden`).

## Status meanings

- **online** — producing frames normally.
- **degraded** — opened but frames are stalling or erroring intermittently.
- **offline** — not producing frames; check the device, USB, or permissions.

## Camera identity across the fleet

Each camera has an `id` (often device-path-like, e.g. `/dev/video0`) and, for
peers, a `host` and `proxy_prefix` so the dashboard can route streams through the
owning node. See [Fleet](fleet) for how cross-node viewing works.
