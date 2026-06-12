"""Resolve config/data/media locations across Linux, macOS, and Windows.

All paths can be overridden with ``TAILCAM_DATA_DIR`` and ``TAILCAM_CONFIG`` so
that the systemd/launchd service and the test suite can point TailCam at an
isolated directory.

Data from a pre-rename *AnyCam* install is brought across by an explicit,
one-time migration (see :mod:`tailcam.migrate`) — these functions always return
the TailCam location. The ``legacy_*`` helpers expose the old AnyCam paths so
the migration can find what to move.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "tailcam"
APP_NAME_MAC = "TailCam"
LEGACY_APP_NAME = "anycam"
LEGACY_APP_NAME_MAC = "AnyCam"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _config_base() -> Path:
    if _is_windows():
        return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    if _is_macos():
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def _data_base() -> Path:
    if _is_windows():
        return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    if _is_macos():
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def _app_name() -> str:
    return APP_NAME_MAC if (_is_windows() or _is_macos()) else APP_NAME


def config_dir() -> Path:
    override = os.environ.get("TAILCAM_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return _config_base() / _app_name()


def config_file() -> Path:
    override = os.environ.get("TAILCAM_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def data_dir() -> Path:
    override = os.environ.get("TAILCAM_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return _data_base() / _app_name()


def media_dir() -> Path:
    return data_dir() / "media"


def thumbnails_dir() -> Path:
    return media_dir() / "thumbnails"


def database_file() -> Path:
    return data_dir() / "tailcam.db"


def pid_file() -> Path:
    return data_dir() / "tailcam.pid"


# --- pre-rename AnyCam locations (used only by the migration) ---------------


def _legacy_app_name() -> str:
    return LEGACY_APP_NAME_MAC if (_is_windows() or _is_macos()) else LEGACY_APP_NAME


def legacy_config_dir() -> Path:
    return _config_base() / _legacy_app_name()


def legacy_data_dir() -> Path:
    return _data_base() / _legacy_app_name()


def legacy_database_file() -> Path:
    return legacy_data_dir() / "anycam.db"


def ensure_dirs() -> None:
    """Create all runtime directories if they do not yet exist."""
    for path in (config_dir(), data_dir(), media_dir(), thumbnails_dir()):
        path.mkdir(parents=True, exist_ok=True)
