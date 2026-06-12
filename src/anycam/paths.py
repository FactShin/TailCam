"""Resolve config/data/media locations across Linux and macOS.

All paths can be overridden with ``ANYCAM_DATA_DIR`` and ``ANYCAM_CONFIG`` so
that the systemd/launchd service and the test suite can point AnyCam at an
isolated directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "anycam"
APP_NAME_MAC = "AnyCam"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_windows() -> bool:
    return sys.platform == "win32"


def config_dir() -> Path:
    override = os.environ.get("ANYCAM_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if _is_windows():
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME_MAC
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME_MAC
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_NAME


def config_file() -> Path:
    override = os.environ.get("ANYCAM_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def data_dir() -> Path:
    override = os.environ.get("ANYCAM_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if _is_windows():
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME_MAC
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME_MAC
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def media_dir() -> Path:
    return data_dir() / "media"


def thumbnails_dir() -> Path:
    return media_dir() / "thumbnails"


def database_file() -> Path:
    return data_dir() / "anycam.db"


def pid_file() -> Path:
    return data_dir() / "anycam.pid"


def ensure_dirs() -> None:
    """Create all runtime directories if they do not yet exist."""
    for path in (config_dir(), data_dir(), media_dir(), thumbnails_dir()):
        path.mkdir(parents=True, exist_ok=True)
