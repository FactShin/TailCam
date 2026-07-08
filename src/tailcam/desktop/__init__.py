"""TailCam desktop app (issue #38): a menu-bar/tray shell around the dashboard.

Design (see docs/desktop.md): a pure-Python shell — pystray for the menu-bar/
tray, pywebview for an embedded dashboard window — installed as the optional
``tailcam[desktop]`` extra inside the same per-user venv as the server. The
shell never touches cameras itself; it talks to the node over the same REST
API the browser uses and drives the service through tailcam.service.installer.

Module layout keeps ALL GUI imports out of module top level so the pure-logic
core (state/menu/server/nodes/updates) imports and tests fine on a headless
CI box with no GUI backends installed:

- state.py / menu.py       — dataclasses + the pure menu model (fully testable)
- server.py                — local node probe + service lifecycle
- nodes.py / updates.py    — fleet list + self-update actions
- tray.py / window.py      — pystray / pywebview adapters (lazy imports)
- app.py                   — orchestrator + single-instance lock
- macos_bundle.py          — generates ~/Applications/TailCam.app (stdlib+PIL)
"""

from __future__ import annotations


def have_tray() -> tuple[bool, str]:
    """Can the pystray backend load here? (available, detail)."""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on host packages
        return False, f"tray unavailable: {exc}"
    return True, "pystray importable"


def have_webview() -> tuple[bool, str]:
    """Can the pywebview backend load here? (available, detail)."""
    try:
        import webview  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on host packages
        return False, f"webview unavailable: {exc}"
    return True, "pywebview importable"
