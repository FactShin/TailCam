"""Local node: probe the REST API and drive the service lifecycle.

The /api/system probe is the single source of truth for "running" — on
Windows installer.is_installed() is hardcoded True (task existence is checked
by schtasks itself), so service state must never be inferred from it.
"""

from __future__ import annotations

import time

import httpx

from tailcam.config import AppConfig
from tailcam.desktop.state import ServerState
from tailcam.logging_setup import get_logger
from tailcam.service import installer

log = get_logger(__name__)

_PROBE_TIMEOUT = 2.0


class NodeClient:
    """State + actions for one node (the local one, or a remote in client mode)."""

    def __init__(self, base_url: str | None = None) -> None:
        # Client mode: an explicit remote URL (Tailscale Serve HTTPS).
        self._client_url = (base_url or "").rstrip("/") + "/" if base_url else ""
        self._port = 8088
        if not self._client_url:
            try:
                self._port = AppConfig.load().server.port
            except Exception as exc:  # pragma: no cover - unreadable config
                log.warning("could not read config for port: %s", exc)

    @property
    def base_url(self) -> str:
        return self._client_url or f"http://localhost:{self._port}/"

    @property
    def client_mode(self) -> bool:
        return bool(self._client_url)

    def probe(self) -> dict | None:
        """GET /api/system, or None when the node isn't answering."""
        try:
            resp = httpx.get(f"{self.base_url}api/system", timeout=_PROBE_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def update_info(self) -> dict | None:
        try:
            resp = httpx.get(f"{self.base_url}api/update", timeout=_PROBE_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def state(self) -> ServerState:
        system = self.probe()
        update = self.update_info() if system else None
        return ServerState(
            installed=installer.is_installed() if not self.client_mode else False,
            running=system is not None,
            port=self._port,
            version=str(system.get("version", "")) if system else "",
            update_available=bool(update and update.get("available")),
            update_latest=str(update.get("latest") or "") if update else "",
            client_mode=self.client_mode,
            base_url=self.base_url,
        )

    # -- service lifecycle (local mode only) ---------------------------------
    def start(self) -> str:
        return installer.start()

    def stop(self) -> str:
        return installer.stop()

    def restart(self) -> str:
        return installer.restart()

    def install_service(self) -> str:
        return installer.install()

    def wait_running(self, timeout: float = 20.0) -> bool:
        """Poll the probe until the API answers (used after start/restart)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.probe() is not None:
                return True
            time.sleep(0.5)
        return False
