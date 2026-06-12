#!/usr/bin/env bash
# TailCam installer for macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-macos.sh | bash
#
# Installs TailCam into a per-user virtualenv, registers a launchd agent, and
# exposes the dashboard over Tailscale when available. macOS numpy/OpenCV wheels
# bundle their native libraries, so there's no system-library step.
set -eu

REPO="${TAILCAM_REPO:-factshin/tailcam}"
REF="${TAILCAM_REF:-main}"
PORT="${TAILCAM_PORT:-8088}"
DO_SERVICE=1
DO_TAILSCALE=1
VENV_DIR="${HOME}/.local/share/tailcam/venv"
LEGACY_VENV_DIR="${HOME}/.local/share/anycam/venv"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift ;;
        --ref) REF="$2"; shift ;;
        --no-service) DO_SERVICE=0 ;;
        --no-tailscale) DO_TAILSCALE=0 ;;
        -h|--help)
            echo "Usage: install-macos.sh [--port N] [--ref REF] [--no-service] [--no-tailscale]"
            exit 0 ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

[ "$(uname -s)" = "Darwin" ] || { err "This installer is for macOS. Use install-linux.sh or install.ps1."; exit 1; }

# --- Python 3.10+ (install via Homebrew if needed) --------------------------
PYTHON=""
find_python() {
    local c
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$c" >/dev/null 2>&1 && \
           "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
            PYTHON="$(command -v "$c")"; return 0
        fi
    done
    return 1
}
ensure_python() {
    find_python && { log "Using $($PYTHON -V) ($PYTHON)"; return 0; }
    if have brew; then
        log "Installing Python 3.12 via Homebrew"
        brew install python@3.12 || warn "Homebrew install failed."
        find_python && { log "Using $($PYTHON -V) ($PYTHON)"; return 0; }
    fi
    err "Python 3.10+ required. Install Homebrew (https://brew.sh) then: brew install python@3.12"
    exit 1
}

install_tailcam() {
    local spec="git+https://github.com/${REPO}.git@${REF}"
    # Stop a running agent so the upgrade actually takes effect.
    launchctl unload "$HOME/Library/LaunchAgents/com.tailcam.plist" 2>/dev/null || true
    log "Creating virtualenv at ${VENV_DIR}"
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
    "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
    log "Installing TailCam ($spec)"
    "${VENV_DIR}/bin/pip" install "$spec"
    TAILCAM_BIN="${VENV_DIR}/bin/tailcam"
}

# Remove a pre-rename AnyCam install if present: its launchd agent, venv, and
# CLI symlink. Config/media/database are left in place — the first `tailcam`
# run migrates them into the TailCam locations.
remove_legacy_anycam() {
    local plist="$HOME/Library/LaunchAgents/com.anycam.plist"
    [ -d "$LEGACY_VENV_DIR" ] || [ -e "$plist" ] || [ -L "$HOME/.local/bin/anycam" ] || return 0
    log "Removing old AnyCam install"
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    rm -rf "$LEGACY_VENV_DIR"
    rmdir "$(dirname "$LEGACY_VENV_DIR")" 2>/dev/null || true  # only if no data remains
    rm -f "$HOME/.local/bin/anycam"
}

link_cli() {
    # Put `tailcam` on PATH via ~/.local/bin.
    mkdir -p "$HOME/.local/bin"
    ln -sf "${VENV_DIR}/bin/tailcam" "$HOME/.local/bin/tailcam"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "Add ~/.local/bin to your PATH to use 'tailcam' directly (e.g. in ~/.zshrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
}

setup_service() {
    "$TAILCAM_BIN" config --port "$PORT" >/dev/null 2>&1 || true
    [ "$DO_SERVICE" -eq 0 ] && { warn "Skipping service (--no-service)."; return 0; }
    log "Registering launchd agent"
    "$TAILCAM_BIN" install-service || warn "Service registration failed."
    warn "First run may prompt for camera access — approve it in System Settings › Privacy."
}

ensure_tailscale() {
    [ "$DO_TAILSCALE" -eq 0 ] && return 0
    if ! have tailscale && [ ! -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
        warn "Tailscale not found. Install from the App Store or:  brew install tailscale"
        return 0
    fi
    log "Exposing TailCam over Tailscale"
    # `tailcam tailscale serve` checks that Tailscale is running and messages if not.
    "$TAILCAM_BIN" tailscale serve || warn "Run 'tailscale up' then 'tailcam tailscale serve'; the UI is available locally meanwhile."
}

log "Installing TailCam on macOS (ref=${REF}, port=${PORT})"
ensure_python
install_tailcam
remove_legacy_anycam
link_cli
setup_service
ensure_tailscale
echo
log "TailCam installed."
"$TAILCAM_BIN" status || true
echo
log "Open the web UI at one of the URLs above."
