#!/usr/bin/env bash
# AnyCam installer — curl -fsSL https://raw.githubusercontent.com/factshin/anycam/main/install.sh | bash
#
# Installs AnyCam into an isolated environment, registers a user service, and
# (when Tailscale is running) exposes the web UI over your tailnet.
set -eu

REPO="${ANYCAM_REPO:-factshin/anycam}"
REF="${ANYCAM_REF:-main}"
PORT="${ANYCAM_PORT:-8088}"
ASSUME_YES=0
DO_SERVICE=1
DO_TAILSCALE=1

VENV_DIR="${HOME}/.local/share/anycam/venv"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; }

usage() {
    cat <<'EOF'
AnyCam installer

Options:
  --yes            Non-interactive; auto-install system packages where possible
  --port PORT      Web UI port (default 8088)
  --ref REF        Git ref/tag to install (default main)
  --no-service     Do not register the background service
  --no-tailscale   Skip Tailscale detection/serve
  -h, --help       Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --yes) ASSUME_YES=1 ;;
        --port) PORT="$2"; shift ;;
        --ref) REF="$2"; shift ;;
        --no-service) DO_SERVICE=0 ;;
        --no-tailscale) DO_TAILSCALE=0 ;;
        -h|--help) usage; exit 0 ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

OS="$(uname -s)"
DISTRO=""
if [ "$OS" = "Linux" ] && [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    DISTRO="$(. /etc/os-release && echo "${ID:-}")"
fi

have() { command -v "$1" >/dev/null 2>&1; }

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    if [ ! -t 0 ]; then return 1; fi   # non-interactive pipe: decline prompts
    printf '%s [y/N] ' "$1"
    read -r reply
    case "$reply" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# --- Python -----------------------------------------------------------------
# Resolved interpreter (>=3.10) is stored in $PYTHON and used for the venv.
PYTHON=""

py_ok() {
    # Succeeds if "$1" is a Python interpreter of version >= 3.10.
    "$1" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
    local cand
    for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cand" >/dev/null 2>&1 && py_ok "$cand"; then
            PYTHON="$(command -v "$cand")"
            return 0
        fi
    done
    return 1
}

ensure_python() {
    if find_python; then
        log "Using Python $("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])') ($PYTHON)"
        return 0
    fi
    warn "Python 3.10+ not found (system python3 is $(python3 --version 2>/dev/null || echo 'absent'))."
    case "$DISTRO" in
        debian|ubuntu)
            if confirm "Install Python 3 via apt?"; then
                sudo apt-get update -y && sudo apt-get install -y python3 python3-venv python3-pip || \
                    warn "apt install failed."
            fi ;;
        *)
            if [ "$OS" = "Darwin" ] && have brew; then
                if confirm "Install Python 3.12 via Homebrew?"; then
                    brew install python@3.12 || warn "Homebrew install failed."
                fi
            fi ;;
    esac
    if find_python; then
        log "Using Python $("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])') ($PYTHON)"
        return 0
    fi
    err "Python 3.10+ is required and could not be installed automatically."
    case "$DISTRO" in
        debian|ubuntu) echo "  Try: sudo apt-get install -y python3 python3-venv python3-pip" ;;
        *) [ "$OS" = "Darwin" ] && echo "  Try: brew install python@3.12" ;;
    esac
    exit 1
}

# --- System dependencies ----------------------------------------------------
# numpy's Linux wheels dynamically link OpenBLAS (libopenblas.so.0) and OpenCV
# needs libGL/glib — these are REQUIRED for AnyCam to import, so we install them
# by default rather than behind a prompt (a piped `curl | bash` has no TTY to
# answer one). Optional extras (ffmpeg, v4l-utils) are attempted but tolerated.
ensure_system_deps() {
    if [ "$OS" = "Darwin" ]; then
        # macOS numpy/opencv wheels bundle their native libs; only ffmpeg is extra.
        if have brew && ! have ffmpeg && [ "$ASSUME_YES" -eq 1 ]; then
            brew install ffmpeg || true
        fi
        return 0
    fi
    case "$DISTRO" in
        debian|ubuntu) ;;
        *) warn "Unknown distro; if AnyCam fails to import, install libopenblas0, libgl1, libglib2.0-0."; return 0 ;;
    esac

    local required="python3-venv libgl1 libglib2.0-0 libopenblas0"
    local optional="ffmpeg v4l-utils"

    if ! sudo -n true 2>/dev/null && [ ! -t 0 ]; then
        warn "System libraries are required but sudo can't prompt in a piped install."
        echo "    Run this once, then re-run the installer (or just 'anycam status'):"
        echo "    sudo apt-get update && sudo apt-get install -y ${required} ${optional}"
        return 0
    fi

    log "Installing required system libraries: ${required}"
    sudo apt-get update -y || warn "apt-get update failed."
    sudo apt-get install -y $required \
        || warn "Could not install ${required}. AnyCam may fail to import numpy/cv2 — install them manually."
    sudo apt-get install -y $optional || true  # nice-to-have (recording, v4l tooling)
}

# --- Tailscale --------------------------------------------------------------
ensure_tailscale() {
    [ "$DO_TAILSCALE" -eq 0 ] && { warn "Skipping Tailscale (--no-tailscale)."; return 0; }
    if have tailscale; then
        log "Found Tailscale"
    else
        warn "Tailscale not found."
        case "$DISTRO" in
            debian|ubuntu)
                if confirm "Install Tailscale now?"; then
                    curl -fsSL https://tailscale.com/install.sh | sh || warn "Tailscale install failed."
                fi ;;
            *) [ "$OS" = "Darwin" ] && echo "  Install Tailscale from the App Store or: brew install tailscale" ;;
        esac
    fi
    if have tailscale; then
        if ! tailscale status >/dev/null 2>&1; then
            warn "Tailscale is installed but not logged in. Run: sudo tailscale up"
            DO_TAILSCALE=0
        fi
    else
        DO_TAILSCALE=0
    fi
}

# --- Install AnyCam ---------------------------------------------------------
install_anycam() {
    local spec="git+https://github.com/${REPO}.git@${REF}"
    if have pipx; then
        log "Installing AnyCam with pipx"
        pipx install --force --python "$PYTHON" "$spec"
        ANYCAM_BIN="$(command -v anycam || echo "${HOME}/.local/bin/anycam")"
    else
        log "Creating virtualenv at ${VENV_DIR}"
        rm -rf "$VENV_DIR"   # recreate so a stale/old-Python venv can't linger
        "$PYTHON" -m venv "$VENV_DIR"
        "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
        log "Installing AnyCam from ${spec}"
        "${VENV_DIR}/bin/pip" install "$spec"
        ANYCAM_BIN="${VENV_DIR}/bin/anycam"
    fi
}

# --- Service ----------------------------------------------------------------
setup_service() {
    # Persist the chosen port so the service and `tailscale serve` both use it.
    "$ANYCAM_BIN" config --port "$PORT" >/dev/null 2>&1 || true
    [ "$DO_SERVICE" -eq 0 ] && { warn "Skipping service registration (--no-service)."; return 0; }
    log "Registering background service"
    "$ANYCAM_BIN" install-service || warn "Service registration failed."
}

# --- Tailscale serve --------------------------------------------------------
setup_serve() {
    [ "$DO_TAILSCALE" -eq 0 ] && return 0
    log "Exposing AnyCam over Tailscale"
    "$ANYCAM_BIN" tailscale serve || warn "tailscale serve failed; the UI is still available locally."
}

main() {
    log "Installing AnyCam (ref=${REF}, port=${PORT}) on ${OS}/${DISTRO:-unknown}"
    ensure_python
    ensure_system_deps
    ensure_tailscale
    install_anycam
    setup_service
    setup_serve
    echo
    log "AnyCam installed."
    "$ANYCAM_BIN" status || true
    echo
    log "Open the web UI at one of the URLs above."
}

main
