"""Resolve config/data/media locations across Linux, macOS, and Windows.

All paths can be overridden with ``TAILCAM_DATA_DIR`` and ``TAILCAM_CONFIG`` so
that the systemd/launchd service and the test suite can point TailCam at an
isolated directory. The old ``ANYCAM_*`` variables are still honored so
pre-rename service units keep working.

Rename migration: installs made under the AnyCam name keep their existing
config/data directories (and anycam.db) — we fall back to the legacy location
whenever it exists and the TailCam one doesn't.
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


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


# Markers that prove a directory is actually in use (mere existence isn't
# enough: the installers create e.g. ~/.local/share/tailcam/ for the venv,
# which must not steal the data dir away from a legacy AnyCam install).
_CONFIG_MARKERS = ("config.toml", "config.toml.bad")
_DATA_MARKERS = ("tailcam.db", "anycam.db", "media")


def _prefer_existing(new: Path, legacy: Path, markers: tuple[str, ...]) -> Path:
    """Use the TailCam dir if it holds real content, else a populated
    pre-rename AnyCam dir, else default to the TailCam dir."""
    if any((new / m).exists() for m in markers):
        return new
    if any((legacy / m).exists() for m in markers):
        return legacy
    return new


def config_dir() -> Path:
    override = _env("TAILCAM_CONFIG_DIR", "ANYCAM_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if _is_windows():
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return _prefer_existing(base / APP_NAME_MAC, base / LEGACY_APP_NAME_MAC, _CONFIG_MARKERS)
    if _is_macos():
        base = Path.home() / "Library" / "Application Support"
        return _prefer_existing(base / APP_NAME_MAC, base / LEGACY_APP_NAME_MAC, _CONFIG_MARKERS)
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return _prefer_existing(base / APP_NAME, base / LEGACY_APP_NAME, _CONFIG_MARKERS)


def config_file() -> Path:
    override = _env("TAILCAM_CONFIG", "ANYCAM_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def data_dir() -> Path:
    override = _env("TAILCAM_DATA_DIR", "ANYCAM_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if _is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return _prefer_existing(base / APP_NAME_MAC, base / LEGACY_APP_NAME_MAC, _DATA_MARKERS)
    if _is_macos():
        base = Path.home() / "Library" / "Application Support"
        return _prefer_existing(base / APP_NAME_MAC, base / LEGACY_APP_NAME_MAC, _DATA_MARKERS)
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return _prefer_existing(base / APP_NAME, base / LEGACY_APP_NAME, _DATA_MARKERS)


def media_dir() -> Path:
    return data_dir() / "media"


def thumbnails_dir() -> Path:
    return media_dir() / "thumbnails"


def database_file() -> Path:
    # Pre-rename installs keep their database (don't orphan media/event history).
    legacy = data_dir() / "anycam.db"
    if legacy.exists():
        return legacy
    return data_dir() / "tailcam.db"


def pid_file() -> Path:
    return data_dir() / "tailcam.pid"


def ensure_dirs() -> None:
    """Create all runtime directories if they do not yet exist."""
    for path in (config_dir(), data_dir(), media_dir(), thumbnails_dir()):
        path.mkdir(parents=True, exist_ok=True)
