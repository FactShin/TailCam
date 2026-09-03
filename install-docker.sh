#!/usr/bin/env bash
# TailCam Docker installer.
#
#   curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-docker.sh | bash
#
# Pulls the prebuilt TailCam image and runs it as a container with persistent
# volumes (data, config, and Tailscale node identity). Pass --authkey to join
# your tailnet and serve over Tailscale; otherwise it runs local-only on a port.
#
# Cameras (Linux hosts): by default the host's /dev is bound into the container
# and every video4linux device (char major 81) is allowed, so webcams can be
# hot-plugged and a missing /dev/video0 doesn't stop the container. Pass
# --device to pin specific devices instead (the container then won't start if
# one is missing), or --no-hotplug to skip the /dev bind without pinning any.
#
# Examples:
#   ... | bash -s -- --authkey tskey-auth-xxxx
#   ... | bash -s -- --port 9000 --device /dev/video1
set -eu

IMAGE="${TAILCAM_IMAGE:-ghcr.io/factshin/tailcam:latest}"
NAME="${TAILCAM_CONTAINER:-tailcam}"
PORT="${TAILCAM_PORT:-8088}"
TS_NAME="${TS_HOSTNAME:-tailcam}"
AUTHKEY="${TS_AUTHKEY:-}"
DO_TAILSCALE=1
DEVICES=""
HOTPLUG=1

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --authkey) AUTHKEY="$2"; shift ;;
        --hostname) TS_NAME="$2"; shift ;;
        --port) PORT="$2"; shift ;;
        --device) DEVICES="${DEVICES} $2"; shift ;;
        --image) IMAGE="$2"; shift ;;
        --name) NAME="$2"; shift ;;
        --no-tailscale) DO_TAILSCALE=0 ;;
        --no-hotplug) HOTPLUG=0 ;;
        -h|--help)
            echo "Usage: install-docker.sh [--authkey KEY] [--hostname NAME] [--port N]"
            echo "                         [--device /dev/videoN]... [--no-hotplug]"
            echo "                         [--image REF] [--name NAME] [--no-tailscale]"
            echo "  --device PATH   pass exactly this device (repeatable). Disables the hot-plug"
            echo "                  default; the container won't start if the device is missing."
            echo "  --no-hotplug    don't bind the host /dev (no cameras unless --device is given)"
            echo "  Default on Linux: bind /dev + allow all video4linux devices (hot-plug)."
            exit 0 ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

have docker || { err "Docker is not installed. Install it: https://docs.docker.com/get-docker/"; exit 1; }
docker info >/dev/null 2>&1 || { err "Cannot reach the Docker daemon. Is it running, and do you have permission?"; exit 1; }

# Explicit --device pins devices; otherwise Linux hosts get the hot-plug setup
# (bind /dev + cgroup rule for video4linux) so cameras can come and go.
[ -n "${DEVICES// /}" ] && HOTPLUG=0
[ "$(uname -s)" = "Linux" ] || HOTPLUG=0

log "Pulling ${IMAGE}"
if ! docker pull "$IMAGE"; then
    err "Could not pull ${IMAGE}."
    echo "    The image may not be published yet, or the package is private."
    echo "    Build it from a repo checkout instead:  docker compose up -d --build"
    exit 1
fi

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    log "Replacing existing container '${NAME}' (named volumes are preserved)"
    docker rm -f "$NAME" >/dev/null
fi

# Assemble the run arguments.
set -- -d --name "$NAME" --restart unless-stopped \
       -p "${PORT}:8088" \
       -v tailcam-data:/data \
       -v tailcam-config:/config \
       -v tailcam-tsstate:/var/lib/tailscale

if [ "$DO_TAILSCALE" = 1 ] && [ -n "$AUTHKEY" ]; then
    set -- "$@" -e "TS_AUTHKEY=${AUTHKEY}" -e "TS_HOSTNAME=${TS_NAME}"
    if [ "$HOTPLUG" = 1 ]; then
        # /dev is bound below; allow the tun char device through the cgroup.
        set -- "$@" --device-cgroup-rule 'c 10:200 rwm' --cap-add NET_ADMIN
    elif [ -e /dev/net/tun ]; then
        set -- "$@" --device /dev/net/tun:/dev/net/tun --cap-add NET_ADMIN
    else
        warn "/dev/net/tun not present — Tailscale will use userspace networking."
    fi
elif [ -n "$AUTHKEY" ]; then
    warn "--no-tailscale set; ignoring the provided auth key."
fi

if [ "$HOTPLUG" = 1 ]; then
    set -- "$@" -v /dev:/dev --device-cgroup-rule 'c 81:* rmw'
fi
for d in $DEVICES; do
    set -- "$@" --device "${d}:${d}"
done

set -- "$@" "$IMAGE"

log "Starting TailCam…"
docker run "$@" >/dev/null

echo
if [ "$DO_TAILSCALE" = 1 ] && [ -n "$AUTHKEY" ]; then
    log "TailCam is starting and joining your tailnet as '${TS_NAME}'."
    echo "    Tailnet:  https://${TS_NAME}.<your-tailnet>.ts.net:8443/"
    echo "    Local:    http://localhost:${PORT}/"
else
    log "TailCam is running locally."
    echo "    Open:     http://localhost:${PORT}/"
    echo "    To serve over Tailscale, re-run with:  --authkey tskey-auth-xxxx"
fi
echo "    Logs:     docker logs -f ${NAME}"
echo "    Stop:     docker rm -f ${NAME}   (data is kept in the tailcam-* volumes)"
