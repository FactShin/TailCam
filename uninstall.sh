#!/usr/bin/env bash
# AnyCam uninstaller.
set -eu

VENV_DIR="${HOME}/.local/share/anycam/venv"
DATA_DIR="${ANYCAM_DATA_DIR:-${HOME}/.local/share/anycam}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

find_bin() {
    if [ -x "${VENV_DIR}/bin/anycam" ]; then echo "${VENV_DIR}/bin/anycam";
    elif have anycam; then command -v anycam;
    else echo ""; fi
}

ANYCAM_BIN="$(find_bin)"

log "Removing AnyCam"
if [ -n "$ANYCAM_BIN" ]; then
    "$ANYCAM_BIN" uninstall-service || warn "Could not remove service."
fi

if have tailscale; then
    log "Resetting tailscale serve"
    tailscale serve reset >/dev/null 2>&1 || true
fi

if have pipx && pipx list 2>/dev/null | grep -q anycam; then
    pipx uninstall anycam || true
fi
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    log "Removed virtualenv"
fi

if [ -d "$DATA_DIR" ]; then
    printf 'Delete stored media and database at %s? [y/N] ' "$DATA_DIR"
    read -r reply
    case "$reply" in [yY]*) rm -rf "$DATA_DIR"; log "Deleted ${DATA_DIR}" ;; *) log "Kept ${DATA_DIR}" ;; esac
fi

log "AnyCam uninstalled."
