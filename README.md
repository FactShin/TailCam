# AnyCam

**View any webcam from anywhere over [Tailscale](https://tailscale.com), through a web UI.**

AnyCam turns any Debian/Linux or macOS machine with a webcam into a private,
remotely-viewable camera. Plug in a webcam, open the web UI, and watch it from
any device on your tailnet — with multi-camera support, resolution and zoom
controls, snapshots, recording, and motion detection. Put those old webcams to
good use as a monitoring system.

## Install

Pick the one-liner for your OS — each installer is dedicated to that platform (no
cross-OS guesswork).

**Linux** (Debian/Ubuntu/Raspberry Pi OS):

```bash
curl -fsSL https://raw.githubusercontent.com/factshin/anycam/main/install-linux.sh | bash
```

**macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/factshin/anycam/main/install-macos.sh | bash
```

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/factshin/anycam/main/install.ps1 | iex
```

Each installer:
- checks for Python 3.10+ (Linux also installs the system libraries numpy/OpenCV need),
- installs AnyCam into an isolated virtualenv,
- registers a background **user** service so AnyCam starts automatically —
  systemd `--user` + lingering (Linux), launchd agent (macOS), or a logon
  Scheduled Task (Windows),
- detects Tailscale and, if it's running, exposes the UI over HTTPS with `tailscale serve`.

After install, open the URL printed at the end (your tailnet HTTPS address, or
`http://localhost:8088/` locally).

### Installer options

```bash
# Linux / macOS — download then run with flags:
curl -fsSL .../install-linux.sh -o install-linux.sh && bash install-linux.sh --port 9000 --no-tailscale
```
```powershell
# Windows:
irm .../install.ps1 -OutFile install.ps1 ; .\install.ps1 -Port 9000 -NoTailscale
```

Linux/macOS flags: `--port`, `--ref <tag>`, `--no-service`, `--no-tailscale`.
Windows: `-Port`, `-Ref`, `-NoService`, `-NoTailscale`.

To uninstall: run `uninstall-linux.sh` / `uninstall-macos.sh` / `uninstall.ps1`, or
`anycam uninstall-service` to just remove the background service.

> **Windows note:** the logon Scheduled Task runs AnyCam in your user session (so the webcam is
> accessible) and starts after you log in — the same "user session" model as the Mac/Linux
> services. Camera names need the optional `pygrabber` package (installed automatically); without
> it cameras show as "Camera 0/1…".

## Manual install

```bash
pipx install git+https://github.com/factshin/anycam.git
# or
python3 -m venv .venv && .venv/bin/pip install git+https://github.com/factshin/anycam.git
anycam run
```

## Usage

| Command | Description |
| --- | --- |
| `anycam run` | Start the web server |
| `anycam status` | Cameras + tailnet nodes (Rich table) and the access URL |
| `anycam doctor` | Diagnostics: Python, OpenCV, cameras, Tailscale, fleet reachability |
| `anycam cameras` | List detected cameras |
| `anycam start` / `stop` / `restart` | Control the background service |
| `anycam install-service` / `uninstall-service` | Register/remove the background service |
| `anycam tailscale serve` / `serve-off` / `status` | Manage tailnet exposure |
| `anycam config [--init] [--port N] [--serve-port N] [--host H]` | Show or update config |

Tab-completion: `anycam --install-completion`.

## Ports & Tailscale

There are two separate ports:

- **`server.port`** (default **8088**) — the local web UI: `http://localhost:8088/`.
- **`tailscale.serve_port`** (default **8443**) — the tailnet-facing HTTPS port.

AnyCam serves on **`https://<host>.<tailnet>.ts.net:8443/`** by default rather than the
root (`:443`), so it won't clobber another app (e.g. OpenClaw) already served at the root
URL. Tailscale permits `443`, `8443`, and `10000` for serve/funnel.

To change the tailnet port:

```bash
anycam tailscale serve --https-port 10000   # one-off + saved to config
# or edit ~/.config/anycam/config.toml  ([tailscale] serve_port = 10000) and restart the service
```

If AnyCam previously grabbed the root URL and you want it back for another app:

```bash
anycam tailscale serve-off --https-port 443   # removes only AnyCam's :443 handler
```

## Multi-host: every camera, from any device

Install AnyCam on more than one machine on the same tailnet (a Raspberry Pi, a Mac, a Linux
box…) and **each node automatically discovers the others and shows all of their cameras**. Open
the dashboard on any device and you see every camera across your tailnet in one place — no matter
which machine the webcam is physically plugged into.

How it works:

- The node you're viewing becomes an **aggregator**: it finds the other AnyCam nodes, asks each
  for its camera list, merges them, and **reverse-proxies** the remote video and controls — so
  your browser only ever talks to the node you opened (one origin, one Tailscale cert).
- **Discovery is automatic** over Tailscale (it probes online tailnet peers for a running AnyCam).
  You can also list peers explicitly:

  ```toml
  # ~/.config/anycam/config.toml
  [peers]
  auto_discover = true
  static = ["https://anycam-pi.your-tailnet.ts.net:8443"]   # optional explicit peers
  ```

- Name a node with `ANYCAM_HOST` (otherwise its Tailscale MagicDNS name / hostname is used), e.g.
  `ANYCAM_HOST=garage-pi`.
- `GET /api/hosts` lists every node (local + peers) and their camera counts.

Notes & current limits:
- Remote cameras are fully viewable **and** controllable (resolution, zoom/pan, snapshot, record).
- This treats every AnyCam node on your tailnet as trusted — the intended model for a personal
  tailnet (there is no separate auth; Tailscale is the security boundary).
- For now, the **gallery and motion-event feed are per-host** (snapshots/recordings live on the
  node that captured them). Cross-host media aggregation is planned next.
- Linux, macOS, **and Windows** nodes all participate in the same tailnet dashboard.

## Features

- **Polished dashboard (PWA)** — a responsive React web app (installable on phone or desktop) with
  a live camera grid grouped by device, a mobile-first camera view with pinch/zoom, gallery, and
  motion events. Built and shipped inside the package; see [`web-ui/`](web-ui/).
- **Multi-host aggregation** — see every camera across all your tailnet devices from any one of them.
- **Multi-camera** — auto-detects connected webcams; name them and view them in a grid.
- **Resolution, zoom & pan** — set capture resolution; per-viewer digital zoom + pan;
  rotate/flip; brightness/contrast/FPS controls.
- **Snapshots & recording** — capture stills and record clips to disk, with a gallery.
- **Motion detection** — detect motion, log events, and optionally auto-record.
- **Tailscale-native** — secure access over your tailnet; fully usable on a LAN too.

## Architecture

One capture thread per camera publishes the latest frame into a lock-light
`FrameBuffer`; FastAPI serves an MJPEG stream (`multipart/x-mixed-replace`) with
JPEG encoding offloaded to a thread pool so the event loop never blocks. The
streaming layer sits behind a `StreamBackend` abstraction, so a low-latency
WebRTC backend can be added later without changing capture or the web layer.
Settings live in a TOML config; the camera registry, media index, and motion
events live in SQLite.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ANYCAM_SYNTHETIC=1 anycam run      # run without a physical webcam
pytest                             # tests use a synthetic camera, no hardware needed
ruff check . && mypy src
```

Set `ANYCAM_SYNTHETIC=1` to use a built-in synthetic camera source — useful on
headless servers, in containers, and in CI where no webcam exists.

The dashboard front-end lives in [`web-ui/`](web-ui/) (React + Vite). Its build
output is committed to `src/anycam/web/spa/` and ships in the wheel, so end users
never need Node. To change the UI: `cd web-ui && npm install && npm run build`,
then commit both the source and the regenerated `src/anycam/web/spa/`.

**Releases:** bump `__version__` in `src/anycam/__init__.py` with every change
merged to `main`. The version is shown by `anycam version`, `anycam status`,
`/api/system`, and the dashboard Settings page — it's how you confirm a node is
actually running the build you think it is.

## License

MIT
