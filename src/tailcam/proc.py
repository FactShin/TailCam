"""Subprocess helpers that never flash a console window on Windows.

TailCam's background service runs under ``pythonw.exe`` (no console). On
Windows, any child process started without ``CREATE_NO_WINDOW`` then allocates
a **new visible console** — so every ``tailscale status`` poll, ffmpeg encode,
or PowerShell call popped a cmd window on the user's desktop. All background
subprocess calls go through here; only intentionally-interactive commands
(e.g. opening the user's editor) should bypass it.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

# 0 everywhere but Windows, so `creationflags |= NO_WINDOW` is always safe.
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """``subprocess.run`` that suppresses the console window on Windows."""
    if NO_WINDOW:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | NO_WINDOW
    return subprocess.run(cmd, **kwargs)  # noqa: S603 - callers pass fixed commands
