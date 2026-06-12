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

import subprocess
import sys
from pathlib import Path

from tailcam.logging_setup import get_logger

log = get_logger(__name__)

SYSTEMD_LABEL = "tailcam.service"
LAUNCHD_LABEL = "com.tailcam"
LEGACY_SYSTEMD_LABEL = "anycam.service"
LEGACY_LAUNCHD_LABEL = "com.anycam"

_SYSTEMD_UNIT = """[Unit]
Description=TailCam webcam server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=3

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
</dict>
</plist>
"""


def _exec_start() -> str:
    return f"{sys.executable} -m tailcam run"


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
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", LEGACY_SYSTEMD_LABEL], check=False
    )
    legacy.unlink(missing_ok=True)
    log.info("Removed legacy %s (renamed to %s)", LEGACY_SYSTEMD_LABEL, SYSTEMD_LABEL)


def _install_systemd() -> str:
    _remove_legacy_systemd()
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SYSTEMD_UNIT.format(exec_start=_exec_start()))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", SYSTEMD_LABEL], check=False)
    # `restart`, not `enable --now`: --now is a no-op when the service is
    # already active, which left upgrades running the OLD code until reboot.
    subprocess.run(["systemctl", "--user", "restart", SYSTEMD_LABEL], check=False)
    return f"Installed systemd user service at {path} (restarted)"


def _uninstall_systemd() -> str:
    _remove_legacy_systemd()
    subprocess.run(["systemctl", "--user", "disable", "--now", SYSTEMD_LABEL], check=False)
    path = _systemd_unit_path()
    path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return "Removed systemd user service"


def _remove_legacy_launchd() -> None:
    legacy = _launchd_plist_path(LEGACY_LAUNCHD_LABEL)
    if not legacy.exists():
        return
    subprocess.run(["launchctl", "unload", str(legacy)], check=False)
    legacy.unlink(missing_ok=True)
    log.info("Removed legacy %s (renamed to %s)", LEGACY_LAUNCHD_LABEL, LAUNCHD_LABEL)


def _install_launchd() -> str:
    _remove_legacy_launchd()
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LAUNCHD_PLIST.format(label=LAUNCHD_LABEL, python=sys.executable))
    subprocess.run(["launchctl", "unload", str(path)], check=False)
    subprocess.run(["launchctl", "load", str(path)], check=False)
    return f"Installed launchd agent at {path}"


def _uninstall_launchd() -> str:
    _remove_legacy_launchd()
    path = _launchd_plist_path()
    subprocess.run(["launchctl", "unload", str(path)], check=False)
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
    subprocess.run(
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
        "$t = New-ScheduledTaskTrigger -AtLogOn; "
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName '{SCHTASK_NAME}' -Action $a -Trigger $t "
        "-Settings $s -Force | Out-Null"
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
        subprocess.run(["launchctl", "load", str(_active_launchd_plist())], check=False)
        return "Started launchd agent"
    label = _active_systemd_label()
    proc = subprocess.run(["systemctl", "--user", "start", label], check=False)
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
        subprocess.run(["launchctl", "unload", str(_active_launchd_plist())], check=False)
        return "Stopped launchd agent (will start again at next login)"
    label = _active_systemd_label()
    proc = subprocess.run(["systemctl", "--user", "stop", label], check=False)
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
        subprocess.run(["launchctl", "unload", path], check=False)
        subprocess.run(["launchctl", "load", path], check=False)
        return "Restarted launchd agent"
    label = _active_systemd_label()
    proc = subprocess.run(["systemctl", "--user", "restart", label], check=False)
    return (
        "Restarted systemd service" if proc.returncode == 0 else "Failed to restart systemd service"
    )
