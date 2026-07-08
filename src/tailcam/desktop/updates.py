"""Self-update actions for the tray (local mode only)."""

from __future__ import annotations

import sys

from tailcam.logging_setup import get_logger

log = get_logger(__name__)


def apply_update() -> str:
    """Upgrade the node in place and restart its service.

    POSIX: pip-upgrade the venv, then restart. Windows: hand off to the
    official installer (venvs are not relocatable there; it swaps safely).
    """
    from tailcam import update as upd
    from tailcam.service import installer

    if sys.platform == "win32":  # pragma: no cover - Windows-only path
        upd.spawn_windows_installer()
        return "Updater launched — TailCam will restart itself."
    if not upd.run_pip_upgrade():
        return "Update failed — see the service log."
    return installer.restart()
