# Running in Docker

TailCam ships a container image so you can run it fully isolated — no Python
environment to manage on the host. The image bundles TailCam, the web dashboard,
[Tailscale](tailscale), and the OpenCV/ffmpeg runtime libraries.

## One-line install

The fastest path on a Linux host with Docker. It pulls the prebuilt image and
starts a container with persistent volumes:

```bash
# local-only (open http://localhost:8088/)
curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-docker.sh | bash

# join your tailnet and serve over Tailscale
curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-docker.sh | bash -s -- --authkey tskey-auth-xxxx
```

Flags: `--authkey KEY`, `--hostname NAME`, `--port N`, `--device /dev/videoN`
(repeatable), `--image REF`, `--name NAME`, `--no-tailscale`. The script
auto-detects `/dev/video0`, replaces any existing container, and keeps your data
in named volumes.

## Prebuilt image

Images are published to the GitHub Container Registry:

```bash
docker pull ghcr.io/factshin/tailcam:latest
```

Tags: `latest` (newest `main`), `X.Y.Z` / `X.Y` (releases), and `sha-<short>`.
Images are multi-arch — `linux/amd64` and `linux/arm64` (Raspberry Pi).

## Quick start (Docker Compose)

From a checkout of the repo:

```bash
# local-only (reach it on the host at http://localhost:8088/)
docker compose up -d

# or join your tailnet and serve over Tailscale
TS_AUTHKEY=tskey-auth-xxxx docker compose up -d
```

`docker-compose.yml` builds the image, persists data in named volumes, and (on
Linux) passes through `/dev/video0`.

## Two ways to run

### Over Tailscale (recommended)

Provide a [Tailscale auth key](https://tailscale.com/kb/1085/auth-keys/) as
`TS_AUTHKEY`. The container starts `tailscaled`, joins your tailnet as
`TS_HOSTNAME` (default `tailcam`), and TailCam serves itself over Tailscale —
exactly like a native install. Reach it at:

```
https://tailcam.<your-tailnet>.ts.net:8443/
```

This is the isolated, full-access path: requests arrive with Tailscale identity,
so admin features and the [MCP](mcp-overview) endpoint work.

### Local-only

Omit `TS_AUTHKEY` and the container runs without Tailscale. Publish the port and
open it on the host:

```bash
docker run -d --name tailcam -p 8088:8088 \
  -v tailcam-data:/data -v tailcam-config:/config \
  --device /dev/video0:/dev/video0 \
  ghcr.io/factshin/tailcam:latest
```

View and camera control work over the published port. Verified-admin features
(node/fleet management, MCP over HTTP) require Tailscale identity, so use the
Tailscale path for those.

## Cameras

Pass each webcam through with `--device` (Compose: the `devices:` list):

```bash
--device /dev/video0:/dev/video0
```

> **Linux hosts only.** Docker Desktop on macOS and Windows runs containers in a
> Linux VM that cannot see host USB cameras. On those platforms, run TailCam
> natively (see [Installation](installation)) or point the container at a network
> camera. You can always test with the synthetic camera: set
> `TAILCAM_SYNTHETIC=1`.

## Tailscale networking modes

- **Kernel networking (preferred):** run with `--device=/dev/net/tun` and
  `--cap-add=NET_ADMIN` (both are in `docker-compose.yml`). Faster.
- **Userspace networking (fallback):** if `/dev/net/tun` isn't available the
  entrypoint automatically uses `--tun=userspace-networking`. No special
  capabilities needed; Serve still works.

## Persistence

Three volumes keep state across restarts and upgrades:

| Mount | Holds |
| --- | --- |
| `/data` | SQLite database + recordings/snapshots/timelapse (`TAILCAM_DATA_DIR`). |
| `/config` | `config.toml` (`TAILCAM_CONFIG_DIR`). |
| `/var/lib/tailscale` | Tailscale node identity — persist it so you don't re-auth. |

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `TS_AUTHKEY` | — | Tailscale auth key. Set it to serve over Tailscale; omit for local-only. |
| `TS_HOSTNAME` | `tailcam` | Tailnet node name. |
| `TS_EXTRA_ARGS` | — | Extra `tailscale up` args (e.g. `--advertise-tags=tag:tailcam`). |
| `TAILCAM_DATA_DIR` | `/data` | Data directory (set in the image). |
| `TAILCAM_CONFIG_DIR` | `/config` | Config directory (set in the image). |
| `TAILCAM_SYNTHETIC` | — | `1` to use a synthetic camera (no hardware). |

The container always binds `0.0.0.0` internally so the published port works;
Tailscale Serve still proxies to loopback, preserving tailnet identity.

## Building the image

```bash
docker build -t tailcam .
```

It's a multi-stage build: a Node stage compiles the dashboard, then a slim Python
stage installs TailCam with Tailscale and the media libraries.

## Updating

```bash
docker compose pull   # or: docker compose build --pull
docker compose up -d
```

Your volumes persist, so config, media, and Tailscale identity carry over.

## Troubleshooting

- **No cameras in the container.** Confirm `--device /dev/video0` is passed and the
  host is Linux. Check `docker logs tailcam` and the [Troubleshooting](troubleshooting)
  page. Test with `TAILCAM_SYNTHETIC=1`.
- **Tailscale won't connect.** Make sure `TS_AUTHKEY` is valid and unexpired.
  Without `/dev/net/tun` + `NET_ADMIN` it uses userspace networking — check
  `docker logs tailcam` for the mode and any `tailscale up` errors.
- **Lost my node identity after recreate.** Persist `/var/lib/tailscale` (the
  Compose file does this with the `tailcam-tsstate` volume).
- **Can't reach admin features over the published port.** That's by design — admin
  and MCP require Tailscale identity. Use the tailnet URL. See [Security](security).
