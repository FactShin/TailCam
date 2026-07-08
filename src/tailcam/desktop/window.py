"""Dashboard window: pywebview in a spawned child process.

pystray owns the main-process event loop (mandatory on macOS, where the
AppKit run loop must live on the main thread), so the webview gets its own
process — the officially documented pywebview + pystray pattern. With no
webview backend installed, we fall back to the default browser: the menu-bar
app stays fully functional either way.
"""

from __future__ import annotations

import multiprocessing
import webbrowser
from importlib import resources
from pathlib import Path

from tailcam.desktop import have_webview
from tailcam.logging_setup import get_logger

log = get_logger(__name__)


def server_down_page() -> str:
    """file:// URL of the packaged 'server is not running' fallback page."""
    path = resources.files("tailcam.desktop") / "assets" / "server_down.html"
    return Path(str(path)).as_uri()


def _run_webview(url: str, title: str) -> None:  # pragma: no cover - child proc
    import webview

    webview.create_window(title, url, width=1280, height=860)
    webview.start()


class DashboardWindow:
    """At most one window process at a time; opening a new URL replaces it."""

    def __init__(self) -> None:
        self._proc: multiprocessing.process.BaseProcess | None = None

    def open(self, url: str, title: str = "TailCam") -> None:
        ok, _detail = have_webview()
        if not ok:
            # No embedded backend — the browser is a perfectly good window.
            webbrowser.open(url)
            return
        self.close()
        # spawn (not fork): required on macOS, and keeps the child free of the
        # parent's AppKit/tray state everywhere else.
        ctx = multiprocessing.get_context("spawn")
        self._proc = ctx.Process(target=_run_webview, args=(url, title), daemon=True)
        self._proc.start()

    def close(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=3.0)
        self._proc = None
