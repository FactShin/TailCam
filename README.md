# TailCam

**View any webcam from anywhere over [Tailscale](https://tailscale.com), through a web UI.**

TailCam turns any Debian/Linux, macOS, or Windows machine with a webcam into a
private, remotely-viewable camera. Plug in a webcam, open the web UI, and watch
it from any device on your tailnet — with multi-camera support, resolution and
zoom controls, snapshots, recording, and motion detection. Put those old
webcams to good use as a monitoring system.

> **Renamed from AnyCam** (v0.5.0). If you previously ran AnyCam, reinstall with
> the one-liner for your OS — your cameras, settings, recordings, and event
> history migrate across automatically (see [Upgrading from AnyCam](#upgrading-from-anycam)).

## Install

Pick the one-liner for your OS — each installer is dedicated to that platform (no
cross-OS guesswork).

**Linux** (Debian/Ubuntu/Raspberry Pi OS):

```bash
curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-linux.sh | bash
```

**macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-macos.sh | bash
```

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/factshin/tailcam/main/install.ps1 | iex
```

Each installer:
- checks for Python 3.10+ (Linux also installs the system libraries numpy/OpenCV need),
- installs TailCam into an isolated virtualenv,
- registers a background **user** service so TailCam starts automatically —
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
`tailcam uninstall-service` to just remove the background service.

> **Windows note:** the logon Scheduled Task runs TailCam in your user session (so the webcam is
> accessible) and starts after you log in — the same "user session" model as the Mac/Linux
> services. Camera names need the optional `pygrabber` package (installed automatically); without
> it cameras show as "Camera 0/1…".

## Manual install

```bash
pipx install git+https://github.com/factshin/tailcam.git
# or
python3 -m venv .venv && .venv/bin/pip install git+https://github.com/factshin/tailcam.git
tailcam run
```

## Usage

| Command | Description |
| --- | --- |
| `tailcam run` | Start the web server |
| `tailcam status` | Cameras + tailnet nodes (Rich table) and the access URL |
| `tailcam doctor` | Diagnostics: Python, OpenCV, cameras, Tailscale, fleet reachability |
| `tailcam cameras` | List detected cameras |
| `tailcam update [--check]` | Update to the latest version (and restart the service) |
| `tailcam start` / `stop` / `restart` | Control the background service |
| `tailcam install-service` / `uninstall-service` | Register/remove the background service |
| `tailcam tailscale serve` / `serve-off` / `status` | Manage tailnet exposure |
| `tailcam config [--init] [--port N] [--serve-port N] [--host H]` | Show or update config |

Tab-completion: `tailcam --install-completion`. `tailcam --version` prints the version.

## Upgrading from AnyCam

AnyCam and TailCam are a clean break — there is no `anycam` command anymore. To
upgrade, just **reinstall** with the one-liner for your OS (above). On its first
run TailCam automatically migrates a previous AnyCam install:

- moves your config, media, and SQLite database into the TailCam locations
  (renaming `anycam.db` → `tailcam.db`) — cameras, settings, recordings, and
  motion-event history all carry over,
- replaces the background service (`anycam.service` / `com.anycam` / task
  "AnyCam") with the TailCam-named equivalent.

The migration is one-time and idempotent. You can also run it explicitly with
`tailcam migrate`.

## Ports & Tailscale

There are two separate ports:

- **`server.port`** (default **8088**) — the local web UI: `http://localhost:8088/`.
- **`tailscale.serve_port`** (default **8443**) — the tailnet-facing HTTPS port.

TailCam serves on **`https://<host>.<tailnet>.ts.net:8443/`** by default rather than the
root (`:443`), so it won't clobber another app (e.g. OpenClaw) already served at the root
URL. Tailscale permits `443`, `8443`, and `10000` for serve/funnel.

To change the tailnet port:

```bash
tailcam tailscale serve --https-port 10000   # one-off + saved to config
# or edit ~/.config/tailcam/config.toml  ([tailscale] serve_port = 10000) and restart the service
```

If TailCam previously grabbed the root URL and you want it back for another app:

```bash
tailcam tailscale serve-off --https-port 443   # removes only TailCam's :443 handler
```

## Multi-host: every camera, from any device

Install TailCam on more than one machine on the same tailnet (a Raspberry Pi, a Mac, a Linux
box…) and **each node automatically discovers the others and shows all of their cameras**. Open
the dashboard on any device and you see every camera across your tailnet in one place — no matter
which machine the webcam is physically plugged into.

How it works:

- The node you're viewing becomes an **aggregator**: it finds the other TailCam nodes, asks each
  for its camera list, merges them, and **reverse-proxies** the remote video and controls — so
  your browser only ever talks to the node you opened (one origin, one Tailscale cert).
- **Discovery is automatic** over Tailscale (it probes online tailnet peers for a running TailCam).
  You can also list peers explicitly:

  ```toml
  # ~/.config/tailcam/config.toml
  [peers]
  auto_discover = true
  static = ["https://tailcam-pi.your-tailnet.ts.net:8443"]   # optional explicit peers
  ```

- Name a node with `TAILCAM_HOST` (otherwise its Tailscale MagicDNS name / hostname is used), e.g.
  `TAILCAM_HOST=garage-pi`.
- `GET /api/hosts` lists every node (local + peers) and their camera counts.

Notes & current limits:
- Remote cameras are fully viewable **and** controllable (resolution, zoom/pan, snapshot, record).
- This treats every TailCam node on your tailnet as trusted — the intended model for a personal
  tailnet (there is no separate auth; Tailscale is the security boundary).
- For now, the **gallery and motion-event feed are per-host** (snapshots/recordings live on the
  node that captured them). Cross-host media aggregation is planned next.
- Linux, macOS, **and Windows** nodes all participate in the same tailnet dashboard.

## Security model

TailCam's boundary is your **Tailscale network**: the server binds to `127.0.0.1` and is reached
over your tailnet, with no per-request login. On top of that, TailCam ships defense-in-depth:

- **Cross-origin / drive-by protection** — state-changing requests (snapshot, record, delete,
  settings) from a foreign web origin are rejected; only localhost and your tailnet (`*.ts.net`)
  may mutate. This stops a malicious site you visit from poking your local TailCam (CSRF / DNS
  rebinding).
- **Security headers** on every response — `Content-Security-Policy` (same-origin only),
  `X-Frame-Options`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`.
- **No SSRF amplification** — the peer reverse-proxy does not follow redirects.
- No accounts, tokens, telemetry, or third-party calls (except checking GitHub for updates).

Keep the default `127.0.0.1` bind — don't expose TailCam directly to a public network; let
Tailscale handle access.

## AI motion analysis (local, optional)

Motion detection is free pixel-diff; when it fires, TailCam can ask a **local
[Ollama](https://ollama.com) vision model** to label the event (person / animal /
vehicle / package / …) with a short description — no cloud. Because cheap motion
*gates* the model, it's only consulted a frame or two per event, so one machine
can analyze the whole fleet.

Set up (e.g. on a Mac mini):

```bash
# 1. Install Ollama and pull a vision model (moondream is small + fast):
ollama pull moondream          # or: qwen2.5vl / llava for better labels
# 2. In ~/.config/tailcam/config.toml (or the macOS app-support path):
```
```toml
[ai]
enabled = true
base_url = "http://localhost:11434"   # or a tailnet host to analyze the fleet
model = "moondream"
```
```bash
tailcam restart
```

Then motion events show a label chip (🧍 person, 🚗 vehicle…) + the trigger
thumbnail. Settings → **AI motion analysis** shows whether Ollama is reachable
and the model is pulled. To let one node analyze another's events, point
`base_url` at that node (and run Ollama with `OLLAMA_HOST=0.0.0.0` so the tailnet
can reach it). See [`docs/ai-detection-plan.md`](docs/ai-detection-plan.md) for
the roadmap (notifications, 3D-print failure detection).

## Features

- **Polished dashboard (PWA)** — a responsive React web app (installable on phone or desktop) with
  a live camera grid grouped by device, a video wall, a command palette (Cmd/Ctrl+K), a
  mobile-first camera view with pinch/zoom, gallery, and motion events. Built and shipped inside
  the package; see [`web-ui/`](web-ui/).
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
TAILCAM_SYNTHETIC=1 tailcam run    # run without a physical webcam
pytest                             # tests use a synthetic camera, no hardware needed
ruff check . && mypy src
```

Set `TAILCAM_SYNTHETIC=1` to use a built-in synthetic camera source — useful on
headless servers, in containers, and in CI where no webcam exists.

The dashboard front-end lives in [`web-ui/`](web-ui/) (React + Vite). Its build
output is committed to `src/tailcam/web/spa/` and ships in the wheel, so end users
never need Node. To change the UI: `cd web-ui && npm install && npm run build`,
then commit both the source and the regenerated `src/tailcam/web/spa/`.

**Releases:** bump `__version__` in `src/tailcam/__init__.py` **and** the
`version` in `web-ui/package.json` (keep them identical) with every change
merged to `main`; a test enforces they match. The version is shown by `tailcam
version` (and `tailcam --version`), `tailcam status`, `/api/system`, and the
dashboard Settings page — it's how you confirm a node is actually running the
build you think it is.

## License

MIT
