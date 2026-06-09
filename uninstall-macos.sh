#!/usr/bin/env bash
# AnyCam uninstaller for macOS.
set -eu

VENV_DIR="${HOME}/.local/share/anycam/venv"
DATA_DIR="${ANYCAM_DATA_DIR:-${HOME}/Library/Application Support/AnyCam}"
BIN=""
[ -x "${VENV_DIR}/bin/anycam" ] && BIN="${VENV_DIR}/bin/anycam"
[ -z "$BIN" ] && command -v anycam >/dev/null 2>&1 && BIN="$(command -v anycam)"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

log "Removing AnyCam"
if [ -n "$BIN" ]; then
    "$BIN" uninstall-service || true
    "$BIN" tailscale serve-off >/dev/null 2>&1 || true
fi
[ -d "$VENV_DIR" ] && { rm -rf "$VENV_DIR"; log "Removed virtualenv"; }

if [ -d "$DATA_DIR" ]; then
    printf 'Delete stored media and database at %s? [y/N] ' "$DATA_DIR"
    read -r reply
    case "$reply" in [yY]*) rm -rf "$DATA_DIR"; log "Deleted ${DATA_DIR}" ;; *) log "Kept ${DATA_DIR}" ;; esac
fi
log "AnyCam uninstalled."
