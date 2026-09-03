"""Render and (un)register the TailCam background service.

Uses a *user* service on both platforms so no root is required:
- Linux: systemd user unit at ~/.config/systemd/user/tailcam.service
- macOS: launchd agent at ~/Library/LaunchAgents/com.tailcam.plist

Rename migration: installs made under the AnyCam name registered
anycam.service / com.anycam / task "AnyCam". ``install()`` removes those
legacy units so two services never fight over the port, and the control
commands (start/stop/restart) operate on whichever unit is present.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tailcam.logging_setup import get_logger
from tailcam.proc import run as run_hidden

log = get_logger(__name__)

SYSTEMD_LABEL = "tailcam.service"
LAUNCHD_LABEL = "com.tailcam"
LEGACY_SYSTEMD_LABEL = "anycam.service"
LEGACY_LAUNCHD_LABEL = "com.anycam"

# No After=/Wants=network-online.target: that target only exists in the
# *system* manager. In a --user manager it is an unknown unit, so the
# dependency was silently ignored (and logged as a warning). TailCam binds
# 0.0.0.0/loopback and retries Tailscale itself, so it needs no network gate.
_SYSTEMD_UNIT = """[Unit]
Description=TailCam webcam server

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=3
# Stopping finalizes every active recording (encoder flush); give it room so
# a restart never SIGKILLs ffmpeg mid-file.
TimeoutStopSec=180
# glibc creates a malloc arena per thread; TailCam runs a thread per camera,
# stream, and job, which on a 1 GB Pi quietly ate 100+ MB. Two arenas is plenty.
Environment=MALLOC_ARENA_MAX=2
# Keep OpenCV/BLAS from spawning a thread pool per core for tiny operations.
Environment=OMP_NUM_THREADS=2
Environment=OPENBLAS_NUM_THREADS=1

[Install]
WantedBy=default.target
"""

_LAUNCHD_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>tailcam</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
"""


def _exec_start() -> str:
    # Quoted: systemd splits ExecStart on whitespace, so an unquoted home
    # path with a space ("/home/my user/...") never starts.
    return f'"{sys.executable}" -m tailcam run'


def _run_checked(cmd: list[str]) -> tuple[bool, str]:
    """Run a service-manager command; (ok, stderr-or-empty)."""
    proc = run_hidden(cmd, check=False, capture_output=True, text=True)
    rc = getattr(proc, "returncode", 0)
    err = (getattr(proc, "stderr", None) or "").strip()
    if rc != 0:
        log.warning("%s failed (rc=%s): %s", " ".join(cmd), rc, err)
        return False, err or f"exit code {rc}"
    return True, ""


def _systemd_unit_path(label: str = SYSTEMD_LABEL) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / label


def _launchd_plist_path(label: str = LAUNCHD_LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def install() -> str:
    if sys.platform == "win32":
        return _install_windows()
    if sys.platform == "darwin":
        return _install_launchd()
    return _install_systemd()


def uninstall() -> str:
    if sys.platform == "win32":
        return _uninstall_windows()
    if sys.platform == "darwin":
        return _uninstall_launchd()
    return _uninstall_systemd()


def _remove_legacy_systemd() -> None:
    legacy = _systemd_unit_path(LEGACY_SYSTEMD_LABEL)
    if not legacy.exists():
        return
    run_hidden(
        ["systemctl", "--user", "disable", "--now", LEGACY_SYSTEMD_LABEL], check=False
    )
    legacy.unlink(missing_ok=True)
    log.info("Removed legacy %s (renamed to %s)", LEGACY_SYSTEMD_LABEL, SYSTEMD_LABEL)


def _install_systemd() -> str:
    _remove_legacy_systemd()
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SYSTEMD_UNIT.format(exec_start=_exec_start()))
    # `restart`, not `enable --now`: --now is a no-op when the service is
    # already active, which left upgrades running the OLD code until reboot.
    for step in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", SYSTEMD_LABEL],
        ["systemctl", "--user", "restart", SYSTEMD_LABEL],
    ):
        ok, err = _run_checked(step)
        if not ok:
            return (
                f"FAILED: `{' '.join(step)}` — {err}\n"
                f"Unit written to {path}. Is the user systemd instance running "
                "(loginctl enable-linger, or log in via a session)? Check: "
                f"systemctl --user status {SYSTEMD_LABEL}"
            )
    return f"Installed systemd user service at {path} (restarted)"


def _uninstall_systemd() -> str:
    _remove_legacy_systemd()
    run_hidden(["systemctl", "--user", "disable", "--now", SYSTEMD_LABEL], check=False)
    path = _systemd_unit_path()
    path.unlink(missing_ok=True)
    run_hidden(["systemctl", "--user", "daemon-reload"], check=False)
    return "Removed systemd user service"


def _remove_legacy_launchd() -> None:
    legacy = _launchd_plist_path(LEGACY_LAUNCHD_LABEL)
    if not legacy.exists():
        return
    run_hidden(["launchctl", "unload", str(legacy)], check=False)
    legacy.unlink(missing_ok=True)
    log.info("Removed legacy %s (renamed to %s)", LEGACY_LAUNCHD_LABEL, LAUNCHD_LABEL)


def _install_launchd() -> str:
    _remove_legacy_launchd()
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LAUNCHD_PLIST.format(label=LAUNCHD_LABEL, python=sys.executable))
    run_hidden(["launchctl", "unload", str(path)], check=False)  # may not be loaded
    ok, err = _run_checked(["launchctl", "load", str(path)])
    if not ok:
        return (
            f"FAILED: `launchctl load {path}` — {err}\n"
            f"Check the plist with: plutil -lint {path}"
        )
    return f"Installed launchd agent at {path}"


def _uninstall_launchd() -> str:
    _remove_legacy_launchd()
    path = _launchd_plist_path()
    run_hidden(["launchctl", "unload", str(path)], check=False)
    path.unlink(missing_ok=True)
    return "Removed launchd agent"


SCHTASK_NAME = "TailCam"
LEGACY_SCHTASK_NAME = "AnyCam"


def _windows_pythonw() -> Path:
    """pythonw.exe (no console window), falling back to the current interpreter."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return pythonw if pythonw.exists() else Path(sys.executable)


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _powershell(script: str) -> None:
    run_hidden(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=False
    )


def _remove_legacy_windows() -> None:
    _powershell(
        f"Stop-ScheduledTask -TaskName '{LEGACY_SCHTASK_NAME}' -ErrorAction SilentlyContinue"
    )
    _powershell(
        f"Unregister-ScheduledTask -TaskName '{LEGACY_SCHTASK_NAME}' -Confirm:$false "
        "-ErrorAction SilentlyContinue"
    )


def _install_windows() -> str:
    # Register a per-user logon task via PowerShell. Register-ScheduledTask keeps
    # the executable and its arguments separate, so paths with spaces (e.g.
    # C:\Users\me\AppData\Local) work — unlike `schtasks /TR "<quoted> args>"`,
    # whose quoting mangles such paths and silently breaks the task.
    _remove_legacy_windows()
    exe = _ps_quote(str(_windows_pythonw()))
    script = (
        f"$a = New-ScheduledTaskAction -Execute {exe} -Argument '-m tailcam run'; "
        # Scoped to THIS user: a bare -AtLogOn fires for every account that
        # logs on, and a Principal keeps the task running as (and visible to)
        # the installing user's interactive session rather than a service.
        "$u = \"$env:USERDOMAIN\\$env:USERNAME\"; "
        "$t = New-ScheduledTaskTrigger -AtLogOn -User $u; "
        "$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive; "
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName '{SCHTASK_NAME}' -Action $a -Trigger $t "
        "-Principal $p -Settings $s -Force | Out-Null"
    )
    # Stop any running instance first so upgrades actually swap in the new
    # code (re-registering does not restart an already-running task).
    _powershell(f"Stop-ScheduledTask -TaskName '{SCHTASK_NAME}' -ErrorAction SilentlyContinue")
    _powershell(script)
    _powershell(f"Start-ScheduledTask -TaskName '{SCHTASK_NAME}'")
    return f"Installed Windows logon task '{SCHTASK_NAME}' (restarted)"


def _uninstall_windows() -> str:
    _remove_legacy_windows()
    _powershell(f"Stop-ScheduledTask -TaskName '{SCHTASK_NAME}' -ErrorAction SilentlyContinue")
    _powershell(
        f"Unregister-ScheduledTask -TaskName '{SCHTASK_NAME}' -Confirm:$false "
        "-ErrorAction SilentlyContinue"
    )
    return f"Removed Windows logon task '{SCHTASK_NAME}'"


# --- service control (tailcam start / stop / restart) ------------------------


def _active_systemd_label() -> str:
    """The unit to control: tailcam.service, or the legacy unit if that's
    what's still registered (a node updated in place but not yet migrated)."""
    if _systemd_unit_path().exists():
        return SYSTEMD_LABEL
    if _systemd_unit_path(LEGACY_SYSTEMD_LABEL).exists():
        return LEGACY_SYSTEMD_LABEL
    return SYSTEMD_LABEL


def _active_launchd_plist() -> Path:
    if _launchd_plist_path().exists():
        return _launchd_plist_path()
    legacy = _launchd_plist_path(LEGACY_LAUNCHD_LABEL)
    if legacy.exists():
        return legacy
    return _launchd_plist_path()


def _installed() -> bool:
    if sys.platform == "win32":
        return True  # task existence is checked by schtasks itself
    if sys.platform == "darwin":
        return _active_launchd_plist().exists()
    return _systemd_unit_path(_active_systemd_label()).exists()


def is_installed() -> bool:
    """Whether a TailCam (or legacy AnyCam) service is registered."""
    return _installed()


_NOT_INSTALLED = "Service not installed — run `tailcam install-service` first."


def start() -> str:
    """Start the background service."""
    if not _installed():
        return _NOT_INSTALLED
    if sys.platform == "win32":
        _powershell(f"Start-ScheduledTask -TaskName '{SCHTASK_NAME}' -ErrorAction SilentlyContinue")
        _powershell(
            f"Start-ScheduledTask -TaskName '{LEGACY_SCHTASK_NAME}' -ErrorAction SilentlyContinue"
        )
        return f"Started Windows task '{SCHTASK_NAME}'"
    if sys.platform == "darwin":
        run_hidden(["launchctl", "load", str(_active_launchd_plist())], check=False)
        return "Started launchd agent"
    label = _active_systemd_label()
    proc = run_hidden(["systemctl", "--user", "start", label], check=False)
    return "Started systemd service" if proc.returncode == 0 else "Failed to start systemd service"


def stop() -> str:
    """Stop the background service (it will start again at next login/boot)."""
    if not _installed():
        return _NOT_INSTALLED
    if sys.platform == "win32":
        _powershell(f"Stop-ScheduledTask -TaskName '{SCHTASK_NAME}' -ErrorAction SilentlyContinue")
        _powershell(
            f"Stop-ScheduledTask -TaskName '{LEGACY_SCHTASK_NAME}' -ErrorAction SilentlyContinue"
        )
        return f"Stopped Windows task '{SCHTASK_NAME}'"
    if sys.platform == "darwin":
        # The agent has KeepAlive=true, so `launchctl stop` would respawn it;
        # unload is the real stop (it loads again at next login).
        run_hidden(["launchctl", "unload", str(_active_launchd_plist())], check=False)
        return "Stopped launchd agent (will start again at next login)"
    label = _active_systemd_label()
    proc = run_hidden(["systemctl", "--user", "stop", label], check=False)
    return "Stopped systemd service" if proc.returncode == 0 else "Failed to stop systemd service"


def restart() -> str:
    """Restart the background service (e.g. after changing config)."""
    if not _installed():
        return _NOT_INSTALLED
    if sys.platform == "win32":
        _powershell(f"Stop-ScheduledTask -TaskName '{SCHTASK_NAME}' -ErrorAction SilentlyContinue")
        _powershell(
            f"Stop-ScheduledTask -TaskName '{LEGACY_SCHTASK_NAME}' -ErrorAction SilentlyContinue"
        )
        _powershell(f"Start-ScheduledTask -TaskName '{SCHTASK_NAME}' -ErrorAction SilentlyContinue")
        _powershell(
            f"Start-ScheduledTask -TaskName '{LEGACY_SCHTASK_NAME}' -ErrorAction SilentlyContinue"
        )
        return f"Restarted Windows task '{SCHTASK_NAME}'"
    if sys.platform == "darwin":
        path = str(_active_launchd_plist())
        run_hidden(["launchctl", "unload", path], check=False)
        run_hidden(["launchctl", "load", path], check=False)
        return "Restarted launchd agent"
    label = _active_systemd_label()
    proc = run_hidden(["systemctl", "--user", "restart", label], check=False)
    return (
        "Restarted systemd service" if proc.returncode == 0 else "Failed to restart systemd service"
    )
