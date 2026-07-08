"""Desktop app orchestrator: wires state -> menu -> actions.

Runs pystray on the main thread (macOS requirement), the dashboard window in
a spawned child process, and enforces a single instance — a second launch
asks the running one to open the dashboard, then exits.
"""

from __future__ import annotations

import contextlib
import socket
import threading

from tailcam import paths
from tailcam.desktop import menu as menu_model
from tailcam.desktop.nodes import fetch_nodes
from tailcam.desktop.server import NodeClient
from tailcam.desktop.state import MenuSpec
from tailcam.desktop.window import DashboardWindow, server_down_page
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

# Single-instance + open-signal channel: a localhost TCP socket whose port is
# recorded in the data dir. Works identically on macOS/Linux/Windows.
_LOCK_FILE = "desktop-app.port"
_OPEN_SIGNAL = b"open\n"


class SingleInstance:
    """Bind a localhost socket and advertise its port; or signal the owner."""

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._path = paths.data_dir() / _LOCK_FILE

    def acquire(self) -> bool:
        """True if we're the first instance (and now hold the lock)."""
        if self._signal_existing():
            return False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self._sock = sock
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(str(sock.getsockname()[1]))
        return True

    def _signal_existing(self) -> bool:
        """If a live instance holds the lock, ask it to open the dashboard."""
        try:
            port = int(self._path.read_text().strip())
        except (OSError, ValueError):
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0) as conn:
                conn.sendall(_OPEN_SIGNAL)
            return True
        except OSError:
            return False  # stale lock file — previous instance died

    def serve_open_signals(self, on_open) -> None:
        """Background thread: each incoming connection = 'open the dashboard'."""

        def _loop() -> None:
            assert self._sock is not None
            while True:
                try:
                    conn, _addr = self._sock.accept()
                except OSError:  # socket closed on quit
                    return
                with contextlib.suppress(OSError), conn:
                    conn.recv(16)
                on_open()

        threading.Thread(target=_loop, name="desktop-single-instance", daemon=True).start()

    def release(self) -> None:
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        with contextlib.suppress(OSError):
            self._path.unlink()


class DesktopApp:
    def __init__(self, client_url: str | None = None) -> None:
        self.node = NodeClient(client_url)
        self.window = DashboardWindow()
        self._icon = None  # set by run()

    # -- menu ------------------------------------------------------------------
    def build_specs(self) -> list[MenuSpec]:
        state = self.node.state()
        nodes = fetch_nodes(state.base_url) if state.running else []
        return menu_model.build_menu(state, nodes)

    # -- actions -----------------------------------------------------------------
    def open_dashboard(self) -> None:
        state = self.node.state()
        url = state.dashboard_url if state.running else server_down_page()
        self.window.open(url)

    def dispatch(self, action: str) -> None:
        try:
            self._dispatch(action)
        except Exception as exc:  # a failed action must never kill the tray
            log.warning("desktop action %s failed: %s", action, exc)

    def _dispatch(self, action: str) -> None:
        m = menu_model
        if action == m.OPEN_DASHBOARD:
            self.open_dashboard()
        elif action.startswith(m.OPEN_NODE_PREFIX):
            self.window.open(action[len(m.OPEN_NODE_PREFIX):])
        elif action == m.SERVICE_START:
            log.info(self.node.start())
            self.node.wait_running()
        elif action == m.SERVICE_STOP:
            log.info(self.node.stop())
        elif action == m.SERVICE_RESTART:
            log.info(self.node.restart())
            self.node.wait_running()
        elif action == m.SERVICE_INSTALL:
            log.info(self.node.install_service())
            self.node.wait_running()
        elif action == m.CHECK_UPDATES:
            self.node.update_info()  # menu re-reads state on next open
        elif action == m.APPLY_UPDATE:
            from tailcam.desktop.updates import apply_update

            log.info(apply_update())
        elif action == m.QUIT:
            self.quit()

    def quit(self) -> None:
        self.window.close()
        if self._icon is not None:
            self._icon.stop()

    # -- lifecycle ------------------------------------------------------------
    def run(self, open_window_on_launch: bool = True) -> int:
        from tailcam.desktop import have_tray
        from tailcam.desktop.tray import run_tray

        ok, detail = have_tray()
        if not ok:
            log.error("cannot start the desktop app: %s", detail)
            print(f"Desktop backends unavailable ({detail}).")
            print("Install them with: pip install 'tailcam[desktop]'")
            return 1

        lock = SingleInstance()
        if not lock.acquire():
            print("TailCam is already running in the menu bar — asked it to open the dashboard.")
            return 0
        lock.serve_open_signals(self.open_dashboard)

        def on_ready(icon) -> None:
            self._icon = icon
            if open_window_on_launch:
                self.open_dashboard()

        try:
            run_tray(self.build_specs, self.dispatch, on_ready)
        finally:
            lock.release()
            self.window.close()
        return 0

    # -- headless verification (used by --smoke and CI) --------------------------
    def smoke(self) -> dict:
        """Build the real state + menu model without starting any run loop."""
        state = self.node.state()
        nodes = fetch_nodes(state.base_url) if state.running else []
        specs = menu_model.build_menu(state, nodes)
        return {
            "running": state.running,
            "client_mode": state.client_mode,
            "menu_items": len(specs),
            "actions": [s.action for s in specs if s.action],
        }
