"""Self-update: check GitHub for a newer release and install it.

POSIX (Linux/macOS): pip-install the new build into the current venv in place,
then restart the background service.

Windows: a running ``tailcam.exe`` holds a lock on itself, so pip can't replace
it from within. Instead we spawn the official installer in a detached
PowerShell and exit; it stops the service, wipes/recreates the venv, and
restarts.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

import httpx

from tailcam import __version__

_CACHE_TTL = 3600.0  # don't hammer GitHub from the dashboard banner
_cache: tuple[float, str | None] | None = None

# Self-update fetches the version/zip from the GitHub repo on `main`.
RAW_VERSION_URL = "https://raw.githubusercontent.com/factshin/tailcam/main/src/tailcam/__init__.py"
ZIP_URL = "https://github.com/factshin/tailcam/archive/refs/heads/main.zip"
PS_INSTALL_CMD = "irm https://raw.githubusercontent.com/factshin/tailcam/main/install.ps1 | iex"


def parse_version(v: str) -> tuple[int, ...]:
    """'0.2.4' -> (0, 2, 4); tolerant of suffixes."""
    parts = re.findall(r"\d+", v)[:3]
    return tuple(int(p) for p in parts) if parts else (0,)


def latest_version(timeout: float = 6.0) -> str | None:
    """Fetch the version on main, or None if GitHub is unreachable."""
    try:
        r = httpx.get(RAW_VERSION_URL, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        m = re.search(r'__version__\s*=\s*"([^"]+)"', r.text)
        return m.group(1) if m else None
    except Exception:
        return None


def update_available(use_cache: bool = True) -> tuple[str, str | None, bool]:
    """Returns (current, latest_or_None, newer_available). Result cached 1h."""
    global _cache
    now = time.monotonic()
    if use_cache and _cache and (now - _cache[0]) < _CACHE_TTL:
        latest = _cache[1]
    else:
        latest = latest_version()
        if latest is not None:  # only cache successful lookups
            _cache = (now, latest)
    newer = latest is not None and parse_version(latest) > parse_version(__version__)
    return __version__, latest, newer


def run_pip_upgrade() -> bool:
    """In-place upgrade of the current environment (POSIX)."""
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", ZIP_URL])
    return proc.returncode == 0


def spawn_windows_installer() -> None:
    """Run the official installer detached, so it can replace this process's files."""
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(  # noqa: S603 - fixed command
        ["powershell", "-NoProfile", "-Command", PS_INSTALL_CMD],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
