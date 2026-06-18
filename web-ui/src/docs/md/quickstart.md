# Quick start

This walks you from a fresh install to a live camera you can view from another
device. If you haven't installed yet, see [Installation](installation).

## 1. Start TailCam

```bash
tailcam run
```

On first run TailCam writes a default config file and scans for cameras. Open the
dashboard at `http://localhost:8088/`.

No webcam handy? Start with a synthetic test camera:

```bash
TAILCAM_SYNTHETIC=1 tailcam run
```

## 2. Find your cameras

TailCam auto-discovers connected cameras. They appear on the **Cameras** screen
(the home page). From the command line:

```bash
tailcam cameras
```

If a camera is missing, click **Refresh** on the Cameras screen, or see
[Cameras](cameras) for backends and troubleshooting.

## 3. View a stream

Click any camera tile to open its detail view with the live stream, snapshot, and
recording controls. You can also open the **Video wall** (press `W`) to see every
camera at once.

## 4. Turn on motion detection (optional)

Open a camera's settings and enable **Motion detection**, or set it globally in
[Configuration](configuration). Enable **auto-record** to capture clips when
motion is seen. See [Motion detection](motion-detection).

## 5. Access from another device

Make sure [Tailscale](tailscale) is running on this machine and your other
device. TailCam serves itself over Tailscale automatically:

```
https://<this-host>.<your-tailnet>.ts.net:8443/
```

You'll find the exact URL on the **Settings** screen under access, or via:

```bash
tailcam status
```

## 6. Add more nodes (optional)

Install TailCam on another machine on the same tailnet and it will be discovered
automatically — its cameras appear in your grid. See [Fleet](fleet).

## Keyboard shortcuts

- `1`–`8` — jump to a nav section
- `W` — open the video wall
- `⌘K` / `Ctrl+K` — command palette (search cameras and screens)

## Next steps

- [Cameras](cameras) — settings, transforms, hiding devices
- [AI analysis](ai-analysis) — label motion events locally
- [Training](training) — build your own detection models
- [MCP overview](mcp-overview) — let an AI agent drive TailCam
