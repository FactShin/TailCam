#!/usr/bin/env bash
# TailCam installer for Linux (Debian/Ubuntu/Raspberry Pi OS and friends).
#
#   curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-linux.sh | bash
#
# Installs TailCam into a per-user virtualenv, installs the libraries numpy/OpenCV
# need, registers a systemd --user service (with lingering so it survives reboot),
# and exposes the dashboard over Tailscale when available.
set -eu

REPO="${TAILCAM_REPO:-factshin/tailcam}"
REF="${TAILCAM_REF:-main}"
PORT="${TAILCAM_PORT:-8088}"
DO_SERVICE=1
DO_TAILSCALE=1
DO_TAILSCALE_INSTALL=1
DO_UVC_QUIRK=1
# How long to wait for the browser login after `tailscale up` (seconds).
TS_LOGIN_TIMEOUT="${TAILCAM_TAILSCALE_LOGIN_TIMEOUT:-600}"
VENV_DIR="${HOME}/.local/share/tailcam/venv"
LEGACY_VENV_DIR="${HOME}/.local/share/anycam/venv"
# $USER isn't exported in some non-login/provisioning shells; derive it
# from the effective UID so `set -u` never aborts the install.
USER_NAME="${USER:-$(id -un)}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# Can we run sudo? Passwordless first; otherwise let sudo prompt on the
# controlling terminal. Under `curl … | bash` stdin is the pipe, but sudo
# prompts on /dev/tty, so a piped install can still ask for the password when
# a terminal is attached. Only a truly headless run (no tty) skips sudo steps.
SUDO_CHECKED=""
can_sudo() {
    [ -n "$SUDO_CHECKED" ] && return "$SUDO_CHECKED"
    if sudo -n true 2>/dev/null; then
        SUDO_CHECKED=0
    elif [ -c /dev/tty ] && sudo -v </dev/tty; then
        SUDO_CHECKED=0
    else
        SUDO_CHECKED=1
    fi
    return "$SUDO_CHECKED"
}

DO_DESKTOP=0

while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift ;;
        --ref) REF="$2"; shift ;;
        --no-service) DO_SERVICE=0 ;;
        --no-tailscale) DO_TAILSCALE=0 ;;
        --no-tailscale-install) DO_TAILSCALE_INSTALL=0 ;;
        --no-uvc-quirk) DO_UVC_QUIRK=0 ;;
        --desktop) DO_DESKTOP=1 ;;
        -h|--help)
            echo "Usage: install-linux.sh [--port N] [--ref REF] [--no-service] [--no-tailscale] [--no-tailscale-install] [--desktop]"
            echo "  --no-tailscale          skip everything Tailscale-related"
            echo "  --no-tailscale-install  don't auto-install Tailscale when it's missing (still serve if present)"
            echo "  --no-uvc-quirk          Raspberry Pi: don't set the uvcvideo USB-bandwidth quirk"
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
        *) warn "Non-Debian distro '${DISTRO:-?}'. If TailCam fails to import, install: libopenblas0 libgl1 libglib2.0-0"; return 0 ;;
    esac
    # git: pip installs TailCam from git+https; Pi OS Lite / Ubuntu Server
    # ship without it (install_tailcam falls back to the zip archive if it's
    # still missing afterwards).
    local required="python3-venv python3-pip git libgl1 libglib2.0-0 libopenblas0"
    local optional="ffmpeg v4l-utils"
    if ! can_sudo; then
        warn "Required system libraries need sudo, which isn't available in this session."
        echo "    Run this once, then re-run:  sudo apt-get update && sudo apt-get install -y ${required} ${optional}"
        return 0
    fi
    log "Installing system libraries: ${required}"
    sudo apt-get update -y || warn "apt-get update failed."
    sudo apt-get install -y $required || warn "Failed to install ${required}; TailCam may not import. Install them manually."
    sudo apt-get install -y $optional || true
}

# --- Raspberry Pi: let two USB webcams share one bus --------------------------
# UVC webcams (Logitech C920 family especially) advertise far more USB bandwidth
# than MJPEG actually needs; the kernel reserves what they claim, so the second
# camera's stream is refused with ENOSPC ("No space left on device") and shows
# as offline. quirks=0x80 (UVC_QUIRK_FIX_BANDWIDTH) makes uvcvideo reserve what
# the negotiated format really uses. Takes effect after a reboot (or a module
# reload while no camera is in use).
tune_raspberry_pi() {
    [ "$DO_UVC_QUIRK" -eq 1 ] || return 0
    local model=""
    [ -r /proc/device-tree/model ] && model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
    case "$model" in *[Rr]aspberry*) ;; *) return 0 ;; esac
    local conf=/etc/modprobe.d/tailcam-uvcvideo.conf
    if grep -qs 'quirks' "$conf" 2>/dev/null; then
        return 0
    fi
    if ! can_sudo; then
        warn "Raspberry Pi: the uvcvideo bandwidth fix needs sudo. Run once, then reboot:"
        echo "    echo 'options uvcvideo quirks=0x80' | sudo tee $conf"
        return 0
    fi
    log "Raspberry Pi: enabling the uvcvideo USB-bandwidth fix ($conf)"
    if echo 'options uvcvideo quirks=0x80' | sudo tee "$conf" >/dev/null 2>&1; then
        # Reload now if nothing is streaming; otherwise it applies at next boot.
        # (Called after install_tailcam stopped the service, so the cameras
        # are normally free here.) fuser lives in psmisc, which minimal
        # images lack — without it just try the reload; modprobe -r refuses
        # a module that's in use, so failure is harmless.
        if have fuser && fuser /dev/video* >/dev/null 2>&1; then
            warn "A camera is in use; the uvcvideo bandwidth fix applies after the next reboot."
        else
            sudo modprobe -r uvcvideo 2>/dev/null && sudo modprobe uvcvideo 2>/dev/null \
                && log "uvcvideo reloaded with quirks=0x80" \
                || warn "uvcvideo will use the bandwidth fix after the next reboot."
        fi
    else
        warn "Could not write $conf (run: echo 'options uvcvideo quirks=0x80' | sudo tee $conf)."
    fi
}

# --- Python 3.10+ -----------------------------------------------------------
PYTHON=""
# Prefer the distro's default `python3` (it is what python3-venv/python3-gi
# were installed for); only then try versioned names. A candidate must be
# 3.10+ AND have a working venv module (Debian splits it into python3-venv).
python_ok() {
    command -v "$1" >/dev/null 2>&1 && \
        "$1" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null
}
find_python() {
    local c
    for c in python3 python3.13 python3.12 python3.11 python3.10; do
        if python_ok "$c"; then
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

install_tailcam() {
    local spec backup="${VENV_DIR}.bak"
    local venv_opts=""
    if have git; then
        spec="git+https://github.com/${REPO}.git@${REF}"
    else
        # No git (minimal images, apt step skipped): pip can install straight
        # from GitHub's zip archive of the ref.
        spec="https://github.com/${REPO}/archive/${REF}.zip"
    fi
    # The desktop app's GTK/AppIndicator/WebKit bindings come from apt
    # (python3-gi & co.) and can't be pip-installed, so the venv must see the
    # system site-packages for `tailcam app` to work.
    [ "$DO_DESKTOP" -eq 1 ] && venv_opts="--system-site-packages"
    # Stop a running service so the upgrade actually takes effect (an active
    # process would keep serving the old code from the deleted venv).
    systemctl --user stop tailcam.service 2>/dev/null || true
    # Non-destructive: set the working venv aside and only remove it once the
    # new one builds — a failed upgrade must never brick a node. POSIX venvs
    # bake their absolute path into scripts/pyvenv.cfg, so we build AT the
    # final path (not a temp dir + mv, which would break the shebangs).
    rm -rf "$backup"
    [ -d "$VENV_DIR" ] && mv "$VENV_DIR" "$backup"
    log "Creating virtualenv at ${VENV_DIR}"
    # shellcheck disable=SC2086  # venv_opts is intentionally word-split
    if ! ( "$PYTHON" -m venv $venv_opts "$VENV_DIR" \
           && "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null \
           && { log "Installing TailCam ($spec)"; "${VENV_DIR}/bin/pip" install "$spec"; } ); then
        rm -rf "$VENV_DIR"
        if [ -d "$backup" ]; then
            mv "$backup" "$VENV_DIR"
            systemctl --user start tailcam.service 2>/dev/null || true
            warn "Install failed — restored and restarted the previous version."
        fi
        err "TailCam installation failed (see the pip output above)."
        exit 1
    fi
    rm -rf "$backup"
    TAILCAM_BIN="${VENV_DIR}/bin/tailcam"
}

# Remove a pre-rename AnyCam install if present: its systemd service, venv, and
# CLI symlink. Config/media/database are left in place — the first `tailcam`
# run migrates them into the TailCam locations.
remove_legacy_anycam() {
    local unit="${HOME}/.config/systemd/user/anycam.service"
    [ -d "$LEGACY_VENV_DIR" ] || [ -e "$unit" ] || [ -L "$HOME/.local/bin/anycam" ] || return 0
    log "Removing old AnyCam install"
    systemctl --user disable --now anycam.service 2>/dev/null || true
    rm -f "$unit"
    systemctl --user daemon-reload 2>/dev/null || true
    rm -rf "$LEGACY_VENV_DIR"
    rmdir "$(dirname "$LEGACY_VENV_DIR")" 2>/dev/null || true  # only if no data remains
    rm -f "$HOME/.local/bin/anycam"
}

link_cli() {
    # Put `tailcam` on PATH via ~/.local/bin (on PATH for most shells).
    mkdir -p "$HOME/.local/bin"
    ln -sf "${VENV_DIR}/bin/tailcam" "$HOME/.local/bin/tailcam"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) warn "Add ~/.local/bin to your PATH to use 'tailcam' directly (e.g. add to ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
}

setup_service() {
    "$TAILCAM_BIN" config --port "$PORT" >/dev/null 2>&1 || true
    [ "$DO_SERVICE" -eq 0 ] && { warn "Skipping service (--no-service)."; return 0; }
    log "Registering systemd --user service"
    # install-service exits non-zero (and prints FAILED: …) when systemctl
    # refuses; surface it instead of moving on as if the service were live.
    "$TAILCAM_BIN" install-service \
        || warn "Service registration FAILED (see above). TailCam is installed but not running as a service; fix the cause, then run: tailcam install-service"
    # Lingering lets the user service start at boot without an interactive login
    # (important for a headless Pi). Remember when *we* turned it on so the
    # uninstaller can turn it back off.
    if have loginctl; then
        local linger_marker="${HOME}/.local/share/tailcam/.linger-enabled-by-installer"
        if loginctl show-user "$USER_NAME" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
            :  # already on (by the user or a previous install)
        elif { can_sudo && sudo loginctl enable-linger "$USER_NAME" 2>/dev/null; } \
             || loginctl enable-linger "$USER_NAME" 2>/dev/null; then
            mkdir -p "$(dirname "$linger_marker")" && : > "$linger_marker"
        else
            warn "Could not enable lingering; run: sudo loginctl enable-linger $USER_NAME (so it starts at boot)."
        fi
    fi
}

# Tailscale is what makes TailCam reachable from anywhere, so the installer
# makes sure it's present and logged in before finishing: install it (official
# script), run `tailscale up` (prints a login URL and waits), then keep polling
# until the tailnet says we're connected. Every step degrades to a printed
# command instead of failing the install — the UI always works locally.
ts_backend_state() {
    # "Running" | "NeedsLogin" | "Stopped" | "" (not installed / daemon down)
    local json
    json="$(tailscale status --json 2>/dev/null || true)"
    [ -n "$json" ] || { echo ""; return 0; }
    if [ -n "$PYTHON" ]; then
        printf '%s' "$json" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || echo ""
    else
        printf '%s' "$json" | sed -n 's/.*"BackendState": *"\([A-Za-z]*\)".*/\1/p' | head -n1
    fi
}

install_tailscale() {
    [ "$DO_TAILSCALE_INSTALL" -eq 1 ] || { warn "Tailscale not found (--no-tailscale-install). Install it with:  curl -fsSL https://tailscale.com/install.sh | sh"; return 1; }
    if ! can_sudo; then
        warn "Tailscale is not installed and installing it needs sudo, which can't prompt in a piped install."
        echo "    Run this, then re-run the installer:  curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
        return 1
    fi
    log "Installing Tailscale (official installer)"
    if curl -fsSL https://tailscale.com/install.sh | sh; then
        hash -r 2>/dev/null || true
        have tailscale && return 0
    fi
    warn "Tailscale installation failed. Install it manually:  curl -fsSL https://tailscale.com/install.sh | sh"
    return 1
}

wait_for_tailscale_login() {
    # Runs `tailscale up`, which prints the login URL and blocks until the
    # browser login completes; then confirms the backend is Running.
    local state waited=0
    state="$(ts_backend_state)"
    if [ "$state" = "Running" ]; then
        log "Tailscale is connected."
        return 0
    fi
    if ! can_sudo; then
        warn "Tailscale is installed but not logged in, and 'tailscale up' needs sudo."
        echo "    Run:  sudo tailscale up     (then: tailcam tailscale serve)"
        return 1
    fi
    echo
    log "Tailscale needs to sign in. A login link will appear below — open it on ANY device"
    echo "    (phone, laptop) and approve this machine. The installer waits up to ${TS_LOGIN_TIMEOUT}s."
    echo
    # --timeout makes `up` give up instead of blocking forever on a headless box.
    local up_rc=0
    sudo tailscale up --timeout "${TS_LOGIN_TIMEOUT}s" || up_rc=$?
    state="$(ts_backend_state)"
    if [ "$state" = "Running" ]; then
        log "Tailscale is connected."
        return 0
    fi
    # Only keep polling when `up` actually failed AND the daemon is alive and
    # waiting on a browser login. An empty/Stopped state means tailscaled
    # isn't running (or gave up) — polling for 10 minutes can't fix that.
    if [ "$up_rc" -eq 0 ] || [ "$state" != "NeedsLogin" ]; then
        ts_explain_state "$state"
        return 1
    fi
    while [ "$waited" -lt "$TS_LOGIN_TIMEOUT" ]; do
        state="$(ts_backend_state)"
        if [ "$state" = "Running" ]; then
            echo
            log "Tailscale is connected."
            return 0
        fi
        if [ "$state" != "NeedsLogin" ]; then
            echo
            ts_explain_state "$state"
            return 1
        fi
        sleep 3; waited=$((waited + 3))
        [ $((waited % 30)) -eq 0 ] && echo "    …still waiting for the Tailscale login ($((TS_LOGIN_TIMEOUT - waited))s left)"
    done
    warn "Timed out waiting for the Tailscale login. TailCam works locally; when you've logged in run:"
    echo "    sudo tailscale up && tailcam tailscale serve"
    return 1
}

ts_explain_state() {
    case "$1" in
        "")
            warn "The Tailscale daemon (tailscaled) isn't running, so there's nothing to log in to. Start it, then log in:"
            echo "    sudo systemctl enable --now tailscaled && sudo tailscale up && tailcam tailscale serve" ;;
        Stopped)
            warn "Tailscale is installed but stopped (logged in, not connected). Bring it up, then serve:"
            echo "    sudo tailscale up && tailcam tailscale serve" ;;
        NeedsLogin)
            warn "Tailscale still needs a browser login. Run when convenient:"
            echo "    sudo tailscale up && tailcam tailscale serve" ;;
        *)
            warn "Tailscale is in state '$1'. TailCam works locally; once it's connected run:"
            echo "    tailcam tailscale serve" ;;
    esac
}

ensure_tailscale() {
    [ "$DO_TAILSCALE" -eq 0 ] && return 0
    if ! have tailscale; then
        install_tailscale || return 0
    fi
    wait_for_tailscale_login || return 0
    # `tailscale serve` needs operator rights or root; grant the current user
    # operator so it works without sudo (avoids "Access denied").
    sudo tailscale set --operator="$USER_NAME" 2>/dev/null \
        || warn "If serve is denied, run: sudo tailscale set --operator=$USER_NAME"
    log "Exposing TailCam over Tailscale"
    "$TAILCAM_BIN" tailscale serve \
        || warn "tailscale serve failed (try: sudo tailscale set --operator=$USER_NAME). UI still works locally."
}

# --- AI motion labeling (optional, local Ollama) ----------------------------
setup_desktop_app() {
    # Opt-in (--desktop): most Linux nodes are headless servers. Installs the
    # tray/window system libraries (best-effort), the [desktop] pip extra, and
    # the launcher .desktop entry. `tailcam doctor` explains anything missing.
    [ "$DO_DESKTOP" -eq 1 ] || return 0
    log "Setting up the TailCam desktop app (--desktop)"
    case "$DISTRO" in
        debian|ubuntu|raspbian)
            local gui_deps="python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1"
            if can_sudo; then
                # WebKit GI bindings renamed on Ubuntu 24.04 (webkit2-4.1 vs webkit2gtk-4.1).
                sudo apt-get install -y gir1.2-webkit2-4.1 2>/dev/null || sudo apt-get install -y gir1.2-webkit2gtk-4.1 || warn "WebKitGTK GI bindings unavailable — the dashboard will open in the browser."
                sudo apt-get install -y $gui_deps || warn "GUI libraries failed to install — run 'tailcam doctor' for hints."
            else
                warn "GUI libraries need sudo. Run: sudo apt-get install -y $gui_deps"
            fi ;;
        *) warn "Install your distro's GTK3/WebKit2GTK/AppIndicator GI packages, then run 'tailcam doctor'." ;;
    esac
    "${VENV_DIR}/bin/pip" install --quiet "pywebview>=5" "pystray>=0.19" "pillow>=10"         || { warn "Desktop backends failed to install; retry with: pip install 'tailcam[desktop]'"; return 0; }
    "$TAILCAM_BIN" app install --autostart || warn "Could not create the launcher (run 'tailcam app install' later)."
}

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
        echo "    To label what your cameras see (person / animal / vehicle…), install Ollama:"
        echo "        curl -fsSL https://ollama.com/install.sh | sh"
        echo "    then download a model:"
        echo "        ollama pull ${rec}"
    fi
    echo "    You can also do all of this from the TailCam UI → AI."
}

log "Installing TailCam on Linux (${DISTRO:-unknown}, ref=${REF}, port=${PORT})"
ensure_system_deps
ensure_python
install_tailcam
# After install_tailcam (which stops the service) and before setup_service
# (which restarts it): the uvcvideo reload only works while no camera is open.
tune_raspberry_pi
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
