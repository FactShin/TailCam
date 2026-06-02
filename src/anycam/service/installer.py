"""Render and (un)register the AnyCam background service.

Uses a *user* service on both platforms so no root is required:
- Linux: systemd user unit at ~/.config/systemd/user/anycam.service
- macOS: launchd agent at ~/Library/LaunchAgents/com.anycam.plist
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from anycam.logging_setup import get_logger

log = get_logger(__name__)

SYSTEMD_LABEL = "anycam.service"
LAUNCHD_LABEL = "com.anycam"

_SYSTEMD_UNIT = """[Unit]
Description=AnyCam webcam server
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
        <string>anycam</string>
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
    return f"{sys.executable} -m anycam run"


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_LABEL


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def install() -> str:
    if sys.platform == "darwin":
        return _install_launchd()
    return _install_systemd()


def uninstall() -> str:
    if sys.platform == "darwin":
        return _uninstall_launchd()
    return _uninstall_systemd()


def _install_systemd() -> str:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SYSTEMD_UNIT.format(exec_start=_exec_start()))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", SYSTEMD_LABEL], check=False)
    return f"Installed systemd user service at {path}"


def _uninstall_systemd() -> str:
    subprocess.run(["systemctl", "--user", "disable", "--now", SYSTEMD_LABEL], check=False)
    path = _systemd_unit_path()
    path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return "Removed systemd user service"


def _install_launchd() -> str:
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LAUNCHD_PLIST.format(label=LAUNCHD_LABEL, python=sys.executable))
    subprocess.run(["launchctl", "unload", str(path)], check=False)
    subprocess.run(["launchctl", "load", str(path)], check=False)
    return f"Installed launchd agent at {path}"


def _uninstall_launchd() -> str:
    path = _launchd_plist_path()
    subprocess.run(["launchctl", "unload", str(path)], check=False)
    path.unlink(missing_ok=True)
    return "Removed launchd agent"
