#!/usr/bin/env bash
#
# TailCam container entrypoint.
#
# - With TS_AUTHKEY: start tailscaled, join the tailnet, then run TailCam (which
#   serves itself over Tailscale). Uses kernel networking when /dev/net/tun is
#   available, otherwise falls back to userspace networking.
# - Without TS_AUTHKEY: run local-only (no Tailscale). Reach it on the published
#   port, e.g. http://localhost:8088/.
#
# TailCam always binds 0.0.0.0 inside the container so a published port works;
# Tailscale Serve still proxies to loopback, so tailnet identity is preserved.

set -euo pipefail

log() { echo "[tailcam] $*"; }

start_tailscale() {
  mkdir -p /var/lib/tailscale /var/run/tailscale

  local tun_args=""
  if [ ! -c /dev/net/tun ]; then
    log "/dev/net/tun not available — using userspace networking."
    log "For kernel networking, run with --device=/dev/net/tun --cap-add=NET_ADMIN."
    tun_args="--tun=userspace-networking"
  fi

  log "starting tailscaled…"
  # shellcheck disable=SC2086
  tailscaled \
    --state=/var/lib/tailscale/tailscaled.state \
    --socket=/var/run/tailscale/tailscaled.sock \
    $tun_args &

  # Wait for the daemon socket before bringing the node up.
  local i
  for i in $(seq 1 60); do
    [ -S /var/run/tailscale/tailscaled.sock ] && break
    sleep 0.5
  done

  log "joining tailnet as '${TS_HOSTNAME:-tailcam}'…"
  # shellcheck disable=SC2086
  tailscale up \
    --authkey="${TS_AUTHKEY}" \
    --hostname="${TS_HOSTNAME:-tailcam}" \
    ${TS_EXTRA_ARGS:-}
}

if [ -n "${TS_AUTHKEY:-}" ]; then
  start_tailscale
  log "starting TailCam (served over Tailscale)…"
  exec tailcam run --host 0.0.0.0 "$@"
else
  log "no TS_AUTHKEY set — running local-only (no Tailscale)."
  log "publish the port (e.g. -p 8088:8088) and open http://localhost:8088/"
  exec tailcam run --no-tailscale --host 0.0.0.0 "$@"
fi
