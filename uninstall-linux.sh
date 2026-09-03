#!/usr/bin/env bash
# TailCam uninstaller for Linux. Also cleans up pre-rename AnyCam installs.
set -eu

VENV_DIR="${HOME}/.local/share/tailcam/venv"
LEGACY_VENV_DIR="${HOME}/.local/share/anycam/venv"
# A bash array, not a space-joined string: a data dir containing a space
# (e.g. TAILCAM_DATA_DIR="/mnt/My Cams") would otherwise word-split and
# rm -rf the wrong directory.
DATA_DIRS=()
for d in "${TAILCAM_DATA_DIR:-}" "${ANYCAM_DATA_DIR:-}" \
         "${HOME}/.local/share/tailcam" "${HOME}/.local/share/anycam"; do
    [ -n "$d" ] && [ -d "$d" ] && DATA_DIRS+=("$d")
done
BIN=""
[ -x "${VENV_DIR}/bin/tailcam" ] && BIN="${VENV_DIR}/bin/tailcam"
[ -z "$BIN" ] && [ -x "${LEGACY_VENV_DIR}/bin/anycam" ] && BIN="${LEGACY_VENV_DIR}/bin/anycam"
[ -z "$BIN" ] && command -v tailcam >/dev/null 2>&1 && BIN="$(command -v tailcam)"
[ -z "$BIN" ] && command -v anycam >/dev/null 2>&1 && BIN="$(command -v anycam)"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
USER_NAME="${USER:-$(id -un)}"
LINGER_MARKER="${HOME}/.local/share/tailcam/.linger-enabled-by-installer"
UVC_CONF=/etc/modprobe.d/tailcam-uvcvideo.conf

# Ask on the terminal even under `curl … | bash` (stdin is the pipe there).
ask() {
    local prompt="$1" reply=""
    printf '%s' "$prompt"
    if [ -t 0 ]; then
        read -r reply
    elif [ -c /dev/tty ]; then
        read -r reply </dev/tty || reply=""
    else
        echo "(no terminal — keeping)"
    fi
    printf '%s' "$reply"
}

log "Removing TailCam"
if [ -n "$BIN" ]; then
    "$BIN" uninstall-service || true
    "$BIN" tailscale serve-off >/dev/null 2>&1 || true
fi
# uninstall-service removes both names, but cover an install too old to do so.
systemctl --user disable --now anycam.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/anycam.service"
rm -f "$HOME/.local/bin/tailcam" "$HOME/.local/bin/anycam"
rm -f "$HOME/.local/share/applications/tailcam.desktop" \
      "$HOME/.local/share/icons/hicolor/512x512/apps/tailcam.png" \
      "$HOME/.config/autostart/tailcam-tray.desktop"
for v in "$VENV_DIR" "$LEGACY_VENV_DIR"; do
    [ -d "$v" ] && { rm -rf "$v"; log "Removed virtualenv $v"; }
done

# Undo the install-time system tweaks (best effort; both need sudo).
if [ -f "$UVC_CONF" ]; then
    if sudo -n rm -f "$UVC_CONF" 2>/dev/null || { [ -c /dev/tty ] && sudo rm -f "$UVC_CONF" </dev/tty; }; then
        log "Removed $UVC_CONF (uvcvideo bandwidth quirk; takes effect after a reboot)"
    else
        log "Could not remove $UVC_CONF — run: sudo rm -f $UVC_CONF"
    fi
fi
if [ -f "$LINGER_MARKER" ] && command -v loginctl >/dev/null 2>&1; then
    # Only when the installer turned lingering on; never touch a setting the
    # user (or another service) relies on.
    if sudo -n loginctl disable-linger "$USER_NAME" 2>/dev/null \
       || loginctl disable-linger "$USER_NAME" 2>/dev/null \
       || { [ -c /dev/tty ] && sudo loginctl disable-linger "$USER_NAME" </dev/tty; }; then
        log "Disabled lingering for $USER_NAME (enabled by the installer)"
    else
        log "Could not disable lingering — run: sudo loginctl disable-linger $USER_NAME"
    fi
    rm -f "$LINGER_MARKER"
fi

for d in ${DATA_DIRS[@]+"${DATA_DIRS[@]}"}; do
    [ -d "$d" ] || continue
    reply="$(ask "Delete stored media and database at $d? [y/N] ")"
    case "$reply" in [yY]*) rm -rf "$d"; log "Deleted ${d}" ;; *) log "Kept ${d}" ;; esac
done
log "TailCam uninstalled."
