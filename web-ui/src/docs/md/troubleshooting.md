# Troubleshooting

Start with the built-in diagnostics:

```bash
tailcam doctor
```

It reports resolved paths, detected cameras, Tailscale state, and common
problems. `tailcam status` shows cameras, peers, and access URLs.

## No cameras found

- Click **Refresh** on the Cameras screen or run `tailcam cameras`.
- **Linux:** ensure your user can access `/dev/video*` (often the `video` group).
- **macOS:** grant camera permission to your terminal/Python in System Settings →
  Privacy & Security → Camera.
- **Windows:** allow desktop apps to access the camera in Privacy settings.
- No hardware? Test with `TAILCAM_SYNTHETIC=1 tailcam run`.
- Phantom devices hidden earlier? Use **Restore hidden**. See [Cameras](cameras).

## Console (cmd) windows keep popping up on Windows

Fixed in v0.99.9. TailCam's background service runs windowless, but its helper
processes (`tailscale status` polls, ffmpeg, PowerShell) used to open a visible
console each time they ran — which was constantly. All background subprocesses
now start with the no-window flag, and Tailscale status is cached so it spawns
far less often. Update TailCam and restart the service
(`tailcam service restart`) if you still see them.

## A camera is "offline" or "degraded"

- Another app may be holding the device — close it.
- Use **Restart** on the camera (`POST /api/cameras/{id}/restart`).
- Check `last_error` on the camera (shown in detail view / `GET /api/cameras/{id}`).

## "Port already in use"

TailCam is probably already running as the background service. Check with
`tailcam doctor`, or run on a different port: `tailcam run --port <n>`. See the
[CLI reference](cli).

## Can't reach TailCam from another device

- Confirm Tailscale is up on **both** devices: `tailscale status`.
- Confirm serving: `tailcam tailscale status` and check the access URL in
  **Settings** or `tailcam status`.
- Remember the [two ports](tailscale): the tailnet HTTPS port (8443 default) is
  not the local bind port (8088).
- If you ran `tailcam run --no-tailscale`, it isn't being served — restart without
  that flag.

## AI labels not appearing

- Is `[ai] enabled` true? Check `GET /api/ai` — is Ollama `reachable` and the
  `model_present`?
- Pull the model: `ollama pull moondream` (or use the MCP `pull_ollama_model`).
- Remember AI only runs on **motion events** — enable [motion
  detection](motion-detection) first.
- Cross-host Ollama? Make sure `base_url` points at a reachable tailnet host. See
  [AI analysis](ai-analysis).

## Training page is empty / "engine not installed"

The training engine (Ultralytics/PyTorch) isn't installed. Install it to enable
[Training](training). `start_training_run` returns a clear error until then.

## Timelapse smoothing looks wrong or fails

- The default `ffmpeg` engine works everywhere. If you selected `rife` without
  `rife-ncnn-vulkan` installed, TailCam **falls back to ffmpeg** automatically.
- Check available engines: `GET /api/postprocess`. See [Timelapse](timelapse).

## A peer node isn't showing up

- Both nodes must be on the same tailnet and online.
- Give discovery a moment (peers are cached and refreshed periodically).
- Pin it explicitly via `peers.static` or `TAILCAM_PEERS`. See [Fleet](fleet).

## An agent can't connect (MCP)

- Local: is a node running, and is `TAILCAM_URL` correct? Try `tailcam mcp stdio`
  manually.
- Remote: is `mcp.http_enabled` true, and are you hitting the Tailscale URL with a
  verified identity? Unverified callers get 401. See [MCP security](mcp-security).
- Admin tools denied? The caller's tailnet role isn't `admin`. See
  [Security](security).

## Config got into a bad state

A malformed config is backed up to `*.bad` and defaults are used. Fix it and
`tailcam restart`, or reset with `tailcam config --reset`.

## Still stuck?

Capture `tailcam doctor` output and the server logs (in the data directory). See
the [FAQ](faq) for common questions.

## Every motion event says "no clip"

Before 0.99.11, saving a clip per motion event (`motion.auto_record`) was **off
by default** — events were logged with a thumbnail but no video, and storage
stayed at 0 B. Updating to 0.99.11 turns it on automatically (a one-time
migration). If you still see "no clip" on new events, check **Settings →
Recording & storage → Save a clip for motion events**. Old events keep showing
"no clip" — the video for them was never recorded.

## Windows: camera opens nothing / black stream

Windows exposes cameras through two APIs (DirectShow and Media Foundation), and
some drivers "open" on one but never deliver a frame — common on laptops whose
integrated camera sits next to an IR/Windows Hello camera. Since 0.99.11
TailCam verifies real frames arrive and automatically walks
DirectShow → Media Foundation → any, remembering what worked.

If the camera still shows offline:

1. Close other apps that may hold the camera (Teams, Zoom, the Camera app).
2. Check **Windows Settings → Privacy & security → Camera** and make sure
   **"Let desktop apps access your camera"** is on — TailCam (Python) is a
   desktop app and is blocked when this is off.
3. `tailcam restart` and watch the camera page; the status line under the
   camera shows the exact error TailCam sees.

## Windows on ARM (Surface / Snapdragon X): install fails

TailCam's camera stack (OpenCV) publishes **no native ARM64 Windows wheels**,
so `pip install` fails on native ARM64 Python. The installer handles this
automatically: on an ARM64 PC it selects (or installs) **x64 Python**, which
Windows 11 runs transparently under emulation — all dependencies install and
run normally.

If your install failed:

1. Make sure you're on **Windows 11** — Windows 10 on ARM cannot emulate x64.
2. Re-run the installer (`irm .../install.ps1 | iex`). It skips any native
   ARM64 Python it finds and uses/installs an x64 build instead.
3. Every run writes a full transcript to
   `%LOCALAPPDATA%\TailCam\install-<timestamp>.log` — if it still fails, the
   real error (usually pip's) is in there.

If you installed Python yourself, grab the **Windows installer (64-bit)** from
python.org — not the ARM64 one — until OpenCV ships ARM64 wheels.

## Install window closed before I could read the error

Fixed in 1.1.2: the installer used to `exit` on failure, which closed the
PowerShell window instantly when run via `irm | iex`. It now keeps the window
open (press Enter to close) and always writes the full transcript to
`%LOCALAPPDATA%\TailCam\install-<timestamp>.log`, so the error is never lost
— even for background self-updates.

## Windows: "Fatal error in launcher: Unable to create process"

Fixed in 1.2.2. Installs done with v1.2.1 built the virtualenv at a staging
path and renamed it, but pip's Windows launcher `.exe`s embed the absolute
interpreter path — so `tailcam.exe` pointed at a folder that no longer
existed. Re-run the installer: it now builds the venv at its final path (and
keeps the previous install as an automatic rollback if anything fails).
