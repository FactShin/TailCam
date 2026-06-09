#!/usr/bin/env bash
# AnyCam installer for Linux (Debian/Ubuntu/Raspberry Pi OS and friends).
#
#   curl -fsSL https://raw.githubusercontent.com/factshin/anycam/main/install-linux.sh | bash
#
# Installs AnyCam into a per-user virtualenv, installs the libraries numpy/OpenCV
# need, registers a systemd --user service (with lingering so it survives reboot),
# and exposes the dashboard over Tailscale when available.
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
            echo "Usage: install-linux.sh [--port N] [--ref REF] [--no-service] [--no-tailscale]"
            exit 0 ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

[ "$(uname -s)" = "Linux" ] || { err "This installer is for Linux. Use install-macos.sh or install.ps1."; exit 1; }

DISTRO=""
[ -r /etc/os-release ] && DISTRO="$(. /etc/os-release && echo "${ID:-}")"

# --- system libraries (required for numpy/OpenCV to import) ------------------
ensure_system_deps() {
    case "$DISTRO" in
        debian|ubuntu|raspbian) ;;
        *) warn "Non-Debian distro '${DISTRO:-?}'. If AnyCam fails to import, install: libopenblas0 libgl1 libglib2.0-0"; return 0 ;;
    esac
    local required="python3-venv python3-pip libgl1 libglib2.0-0 libopenblas0"
    local optional="ffmpeg v4l-utils"
    if ! sudo -n true 2>/dev/null && [ ! -t 0 ]; then
        warn "Required system libraries need sudo, which can't prompt in a piped install."
        echo "    Run this once, then re-run:  sudo apt-get update && sudo apt-get install -y ${required} ${optional}"
        return 0
    fi
    log "Installing system libraries: ${required}"
    sudo apt-get update -y || warn "apt-get update failed."
    sudo apt-get install -y $required || warn "Failed to install ${required}; AnyCam may not import. Install them manually."
    sudo apt-get install -y $optional || true
}

# --- Python 3.10+ -----------------------------------------------------------
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
    err "Python 3.10+ not found. Install it:  sudo apt-get install -y python3 python3-venv python3-pip"
    exit 1
}

install_anycam() {
    local spec="git+https://github.com/${REPO}.git@${REF}"
    log "Creating virtualenv at ${VENV_DIR}"
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
    "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
    log "Installing AnyCam ($spec)"
    "${VENV_DIR}/bin/pip" install "$spec"
    ANYCAM_BIN="${VENV_DIR}/bin/anycam"
}

setup_service() {
    "$ANYCAM_BIN" config --port "$PORT" >/dev/null 2>&1 || true
    [ "$DO_SERVICE" -eq 0 ] && { warn "Skipping service (--no-service)."; return 0; }
    log "Registering systemd --user service"
    "$ANYCAM_BIN" install-service || warn "Service registration failed."
    # Lingering lets the user service start at boot without an interactive login
    # (important for a headless Pi).
    if have loginctl; then
        sudo loginctl enable-linger "$USER" 2>/dev/null \
            || loginctl enable-linger "$USER" 2>/dev/null \
            || warn "Could not enable lingering; run: sudo loginctl enable-linger $USER (so it starts at boot)."
    fi
}

ensure_tailscale() {
    [ "$DO_TAILSCALE" -eq 0 ] && return 0
    if ! have tailscale; then
        warn "Tailscale not found. Install it with:  curl -fsSL https://tailscale.com/install.sh | sh"
        return 0
    fi
    if ! tailscale status >/dev/null 2>&1; then
        warn "Tailscale installed but not logged in. Run:  sudo tailscale up"
        return 0
    fi
    log "Exposing AnyCam over Tailscale"
    "$ANYCAM_BIN" tailscale serve || warn "tailscale serve failed; the UI is still available locally."
}

log "Installing AnyCam on Linux (${DISTRO:-unknown}, ref=${REF}, port=${PORT})"
ensure_system_deps
ensure_python
install_anycam
setup_service
ensure_tailscale
echo
log "AnyCam installed."
"$ANYCAM_BIN" status || true
echo
log "Open the web UI at one of the URLs above."
