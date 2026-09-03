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
DO_TAILSCALE_INSTALL=1
TS_LOGIN_TIMEOUT="${TAILCAM_TAILSCALE_LOGIN_TIMEOUT:-600}"
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
        --no-tailscale-install) DO_TAILSCALE_INSTALL=0 ;;
        -h|--help)
            echo "Usage: install-macos.sh [--port N] [--ref REF] [--no-service] [--no-tailscale] [--no-tailscale-install]"
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
    local spec backup="${VENV_DIR}.bak"
    # `git` on a fresh Mac is a stub that pops the Xcode Command Line Tools
    # dialog (and fails under a piped install). Only use git+https when a real
    # git is present; otherwise pip installs from GitHub's zip archive.
    if have git && git --version >/dev/null 2>&1; then
        spec="git+https://github.com/${REPO}.git@${REF}"
    else
        spec="https://github.com/${REPO}/archive/${REF}.zip"
    fi
    # Stop a running agent so the upgrade actually takes effect.
    launchctl unload "$HOME/Library/LaunchAgents/com.tailcam.plist" 2>/dev/null || true
    # Non-destructive: set the working venv aside and only remove it once the
    # new one builds, so a failed upgrade never bricks a node. POSIX venvs bake
    # their absolute path, so we build AT the final path (not temp + mv).
    rm -rf "$backup"
    [ -d "$VENV_DIR" ] && mv "$VENV_DIR" "$backup"
    log "Creating virtualenv at ${VENV_DIR}"
    if ! ( "$PYTHON" -m venv "$VENV_DIR" \
           && "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null \
           && { log "Installing TailCam ($spec)"; "${VENV_DIR}/bin/pip" install "$spec"; } ); then
        rm -rf "$VENV_DIR"
        if [ -d "$backup" ]; then
            mv "$backup" "$VENV_DIR"
            launchctl load "$HOME/Library/LaunchAgents/com.tailcam.plist" 2>/dev/null || true
            warn "Install failed — restored the previous version."
        fi
        err "TailCam installation failed (see the pip output above)."
        exit 1
    fi
    rm -rf "$backup"
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
    "$TAILCAM_BIN" install-service \
        || warn "Service registration FAILED (see above). TailCam is installed but not running as a service; fix the cause, then run: tailcam install-service"
    warn "First run may prompt for camera access — approve it in System Settings › Privacy."
}

setup_desktop_app() {
    # The menu-bar app (issue #38): optional backends + a real TailCam.app in
    # ~/Applications. Best-effort — the server works fine without it.
    log "Installing the TailCam menu-bar app"
    if "${VENV_DIR}/bin/pip" install --quiet "pywebview>=5" "pystray>=0.19" "pillow>=10"; then
        "$TAILCAM_BIN" app install || warn "Could not create TailCam.app (run 'tailcam app install' later)."
    else
        warn "Desktop backends failed to install — skip for now; retry with: pip install 'tailcam[desktop]'"
    fi
}

# Tailscale makes TailCam reachable from anywhere: make sure it's installed and
# logged in before finishing. Prefers the CLI from Homebrew; the App Store app
# (Tailscale.app) is honored when present. Every step degrades to a printed
# command rather than failing the install.
TS_APP_BIN="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
TS_SYSTEM_DAEMON="/Library/LaunchDaemons/com.tailscale.tailscaled.plist"
ts_app_running() { pgrep -qx Tailscale 2>/dev/null || pgrep -qf "Tailscale.app/Contents/MacOS/Tailscale" 2>/dev/null; }
ts_cli() {
    # When the App Store/standalone app is running it owns the connection,
    # so talk to it (the brew CLI would report its own, unrelated daemon).
    if [ -x "$TS_APP_BIN" ] && ts_app_running; then "$TS_APP_BIN" "$@"
    elif have tailscale; then tailscale "$@"
    elif [ -x "$TS_APP_BIN" ]; then "$TS_APP_BIN" "$@"
    else return 127; fi
}
ts_backend_state() {
    local json
    json="$(ts_cli status --json 2>/dev/null || true)"
    [ -n "$json" ] || { echo ""; return 0; }
    printf '%s' "$json" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || echo ""
}

install_tailscale() {
    [ "$DO_TAILSCALE_INSTALL" -eq 1 ] || { warn "Tailscale not found (--no-tailscale-install). Install from the App Store or:  brew install tailscale"; return 1; }
    if have brew; then
        log "Installing Tailscale (brew install tailscale)"
        if brew install tailscale; then
            # The Homebrew CLI needs its daemon registered once (asks for sudo;
            # sudo prompts on /dev/tty, so this works under `curl | bash` too).
            if [ -c /dev/tty ]; then
                sudo tailscaled install-system-daemon </dev/tty || warn "Could not register tailscaled; run: sudo tailscaled install-system-daemon"
            else
                warn "Run once to start the Tailscale daemon:  sudo tailscaled install-system-daemon"
            fi
            return 0
        fi
        warn "brew install tailscale failed."
    fi
    warn "Tailscale not found. Install it from the App Store (Tailscale) or https://tailscale.com/download/mac, sign in, then run: tailcam tailscale serve"
    return 1
}

ts_explain_state() {
    case "$1" in
        "")
            if have tailscale && [ ! -f "$TS_SYSTEM_DAEMON" ]; then
                warn "The Homebrew Tailscale CLI is installed but its daemon isn't registered, so there's nothing to log in to. Run:"
                echo "    sudo tailscaled install-system-daemon && sudo tailscale up && tailcam tailscale serve"
            elif [ -x "$TS_APP_BIN" ]; then
                warn "Tailscale.app isn't running. Open it from Applications, sign in, then run: tailcam tailscale serve"
            else
                warn "The Tailscale daemon isn't running. Start it (sudo launchctl load -w $TS_SYSTEM_DAEMON), then:"
                echo "    sudo tailscale up && tailcam tailscale serve"
            fi ;;
        Stopped)
            warn "Tailscale is installed but stopped (logged in, not connected). Bring it up, then serve:"
            echo "    tailscale up && tailcam tailscale serve" ;;
        NeedsLogin)
            warn "Tailscale still needs a browser login. Run when convenient: tailscale up && tailcam tailscale serve" ;;
        *)
            warn "Tailscale is in state '$1'. TailCam works locally; once it's connected run: tailcam tailscale serve" ;;
    esac
}

wait_for_tailscale_login() {
    local state waited=0 up_rc=0
    state="$(ts_backend_state)"
    if [ "$state" = "Running" ]; then log "Tailscale is connected."; return 0; fi
    if [ -x "$TS_APP_BIN" ] && ts_app_running; then
        # The GUI app handles login itself; the CLI can't drive it.
        warn "Tailscale.app is running but not signed in. Sign in from the menu-bar icon, then run: tailcam tailscale serve"
        return 1
    fi
    if [ -x "$TS_APP_BIN" ] && ! have tailscale; then
        warn "Tailscale.app is installed but not running. Open it, sign in, then run: tailcam tailscale serve"
        return 1
    fi
    if [ -z "$state" ]; then
        # Daemon not running: `tailscale up` would only fail, and polling for
        # 10 minutes can't fix that. Say what to do instead.
        ts_explain_state ""
        return 1
    fi
    echo
    log "Tailscale needs to sign in. A login link will appear below — open it on ANY device"
    echo "    and approve this Mac. The installer waits up to ${TS_LOGIN_TIMEOUT}s."
    echo
    if [ -c /dev/tty ]; then
        # sudo prompts on /dev/tty, so this works under `curl | bash` too.
        sudo tailscale up --timeout "${TS_LOGIN_TIMEOUT}s" </dev/tty || up_rc=$?
    else
        tailscale up --timeout "${TS_LOGIN_TIMEOUT}s" 2>&1 || sudo -n tailscale up --timeout "${TS_LOGIN_TIMEOUT}s" 2>&1 || up_rc=$?
    fi
    state="$(ts_backend_state)"
    if [ "$state" = "Running" ]; then log "Tailscale is connected."; return 0; fi
    # Only poll when `up` failed AND the daemon is alive waiting for a browser
    # login; anything else (daemon gone, Stopped) won't change by waiting.
    if [ "$up_rc" -eq 0 ] || [ "$state" != "NeedsLogin" ]; then
        ts_explain_state "$state"
        return 1
    fi
    while [ "$waited" -lt "$TS_LOGIN_TIMEOUT" ]; do
        state="$(ts_backend_state)"
        if [ "$state" = "Running" ]; then echo; log "Tailscale is connected."; return 0; fi
        if [ "$state" != "NeedsLogin" ]; then echo; ts_explain_state "$state"; return 1; fi
        sleep 3; waited=$((waited + 3))
        [ $((waited % 30)) -eq 0 ] && echo "    …still waiting for the Tailscale login ($((TS_LOGIN_TIMEOUT - waited))s left)"
    done
    warn "Timed out waiting for the Tailscale login. TailCam works locally; once signed in run: tailcam tailscale serve"
    return 1
}

ensure_tailscale() {
    [ "$DO_TAILSCALE" -eq 0 ] && return 0
    if ! have tailscale && [ ! -x "$TS_APP_BIN" ]; then
        install_tailscale || return 0
    fi
    wait_for_tailscale_login || return 0
    log "Exposing TailCam over Tailscale"
    # `tailcam tailscale serve` checks that Tailscale is running and messages if not.
    "$TAILCAM_BIN" tailscale serve || warn "Run 'tailscale up' then 'tailcam tailscale serve'; the UI is available locally meanwhile."
}

# --- AI motion labeling (optional, local Ollama) ----------------------------
ensure_ai_hint() {
    local rec="moondream"
    echo
    log "AI motion labeling (optional)"
    if have ollama; then
        if ollama list 2>/dev/null | grep -qiE 'moondream|llava|minicpm-v|llama3.2-vision|bakllava'; then
            echo "    ✓ Ollama is installed and a vision model is downloaded."
        else
            warn "Ollama is installed, but no vision model is downloaded yet. Get one:"
            echo "        ollama pull ${rec}"
        fi
    else
        echo "    To label what your cameras see (person / animal / vehicle…), install Ollama"
        echo "    from https://ollama.com/download, then download a model:"
        echo "        ollama pull ${rec}"
    fi
    echo "    You can also do all of this from the TailCam UI → AI."
}

log "Installing TailCam on macOS (ref=${REF}, port=${PORT})"
ensure_python
install_tailcam
remove_legacy_anycam
link_cli
setup_service
setup_desktop_app
ensure_tailscale
ensure_ai_hint
echo
log "TailCam installed."
"$TAILCAM_BIN" status || true
echo
log "Open the web UI at one of the URLs above."
