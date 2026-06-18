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

Stream quality is governed by the `[stream]` config: `default_fps` (15),
`jpeg_quality` (80), and `max_width` (1280). See [Configuration](configuration).

## Settings

From a camera's detail view (or via the API) you can change:

- **Name** — a friendly label.
- **Properties** — `width`, `height`, `fps`, and `brightness` / `contrast` /
  `saturation` where the device supports them.
- **Transform** — `rotation` (0/90/180/270), `flip_h`, `flip_v`. Useful for
  upside-down or mirrored mounts.
- **Motion detection** — toggle per camera. See [Motion detection](motion-detection).

Changes are applied live and persisted.

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
