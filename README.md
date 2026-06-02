# AnyCam

**View any webcam from anywhere over [Tailscale](https://tailscale.com), through a web UI.**

AnyCam turns any Debian/Linux or macOS machine with a webcam into a private,
remotely-viewable camera. Plug in a webcam, open the web UI, and watch it from
any device on your tailnet — with multi-camera support, resolution and zoom
controls, snapshots, recording, and motion detection. Put those old webcams to
good use as a monitoring system.

## Install (one-liner)

```bash
curl -fsSL https://raw.githubusercontent.com/factshin/anycam/main/install.sh | bash
```

The installer:
- checks for Python 3.10+ and the system libraries OpenCV needs,
- installs AnyCam into an isolated environment (pipx or a dedicated venv),
- detects Tailscale and, if it's running, exposes the UI over HTTPS on your
  tailnet with `tailscale serve`,
- registers a background **user** service (systemd on Linux, launchd on macOS)
  so AnyCam starts automatically.

After install, open the URL printed at the end (your tailnet HTTPS address, or
`http://localhost:8088/` locally).

### Installer options

```bash
curl -fsSL .../install.sh | bash -s -- --yes --port 9000 --no-tailscale
```

`--yes` (non-interactive), `--port`, `--ref <tag>`, `--no-service`, `--no-tailscale`.

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
| `anycam status` | Show cameras, Tailscale status, and access URL |
| `anycam cameras` | List detected cameras |
| `anycam install-service` / `uninstall-service` | Manage the background service |
| `anycam tailscale serve` / `status` | Manage tailnet exposure |

## Features

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

## License

MIT
