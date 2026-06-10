#!/usr/bin/env bash
# AnyCam installer for macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/factshin/anycam/main/install-macos.sh | bash
#
# Installs AnyCam into a per-user virtualenv, registers a launchd agent, and
# exposes the dashboard over Tailscale when available. macOS numpy/OpenCV wheels
# bundle their native libraries, so there's no system-library step.
set -eu

REPO="${ANYCAM_REPO:-factshin/anycam}"
REF="${ANYCAM_REF:-main}"
PORT="${ANYCAM_PORT:-8088}"
DO_SERVICE=1
DO_TAILSCALE=1
VENV_DIR="${HOME}/.local/share/anycam/venv"

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

install_anycam() {
    local spec="git+https://github.com/${REPO}.git@${REF}"
    # Stop a running agent so the upgrade actually takes effect.
    launchctl unload "$HOME/Library/LaunchAgents/com.anycam.plist" 2>/dev/null || true
    log "Creating virtualenv at ${VENV_DIR}"
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
    "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
    log "Installing AnyCam ($spec)"
    "${VENV_DIR}/bin/pip" install "$spec"
    ANYCAM_BIN="${VENV_DIR}/bin/anycam"
}

link_cli() {
    # Put `anycam` on PATH via ~/.local/bin.
    mkdir -p "$HOME/.local/bin"
    ln -sf "${VENV_DIR}/bin/anycam" "$HOME/.local/bin/anycam"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "Add ~/.local/bin to your PATH to use 'anycam' directly (e.g. in ~/.zshrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
}

setup_service() {
    "$ANYCAM_BIN" config --port "$PORT" >/dev/null 2>&1 || true
    [ "$DO_SERVICE" -eq 0 ] && { warn "Skipping service (--no-service)."; return 0; }
    log "Registering launchd agent"
    "$ANYCAM_BIN" install-service || warn "Service registration failed."
    warn "First run may prompt for camera access — approve it in System Settings › Privacy."
}

ensure_tailscale() {
    [ "$DO_TAILSCALE" -eq 0 ] && return 0
    if ! have tailscale && [ ! -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
        warn "Tailscale not found. Install from the App Store or:  brew install tailscale"
        return 0
    fi
    log "Exposing AnyCam over Tailscale"
    # `anycam tailscale serve` checks that Tailscale is running and messages if not.
    "$ANYCAM_BIN" tailscale serve || warn "Run 'tailscale up' then 'anycam tailscale serve'; the UI is available locally meanwhile."
}

log "Installing AnyCam on macOS (ref=${REF}, port=${PORT})"
ensure_python
install_anycam
link_cli
setup_service
ensure_tailscale
echo
log "AnyCam installed."
"$ANYCAM_BIN" status || true
echo
log "Open the web UI at one of the URLs above."
