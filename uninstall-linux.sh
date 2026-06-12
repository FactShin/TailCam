#!/usr/bin/env bash
# TailCam uninstaller for Linux. Also cleans up pre-rename AnyCam installs.
set -eu

VENV_DIR="${HOME}/.local/share/tailcam/venv"
LEGACY_VENV_DIR="${HOME}/.local/share/anycam/venv"
DATA_DIRS=""
for d in "${TAILCAM_DATA_DIR:-}" "${ANYCAM_DATA_DIR:-}" "${HOME}/.local/share/tailcam" "${HOME}/.local/share/anycam"; do
    [ -n "$d" ] && [ -d "$d" ] && DATA_DIRS="${DATA_DIRS} $d"
done
BIN=""
[ -x "${VENV_DIR}/bin/tailcam" ] && BIN="${VENV_DIR}/bin/tailcam"
[ -z "$BIN" ] && [ -x "${LEGACY_VENV_DIR}/bin/anycam" ] && BIN="${LEGACY_VENV_DIR}/bin/anycam"
[ -z "$BIN" ] && command -v tailcam >/dev/null 2>&1 && BIN="$(command -v tailcam)"
[ -z "$BIN" ] && command -v anycam >/dev/null 2>&1 && BIN="$(command -v anycam)"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

log "Removing TailCam"
if [ -n "$BIN" ]; then
    "$BIN" uninstall-service || true
    "$BIN" tailscale serve-off >/dev/null 2>&1 || true
fi
# uninstall-service removes both names, but cover an install too old to do so.
systemctl --user disable --now anycam.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/anycam.service"
rm -f "$HOME/.local/bin/tailcam" "$HOME/.local/bin/anycam"
for v in "$VENV_DIR" "$LEGACY_VENV_DIR"; do
    [ -d "$v" ] && { rm -rf "$v"; log "Removed virtualenv $v"; }
done

for d in $DATA_DIRS; do
    [ -d "$d" ] || continue
    printf 'Delete stored media and database at %s? [y/N] ' "$d"
    read -r reply
    case "$reply" in [yY]*) rm -rf "$d"; log "Deleted ${d}" ;; *) log "Kept ${d}" ;; esac
done
log "TailCam uninstalled."
