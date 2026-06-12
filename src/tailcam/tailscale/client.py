"""Thin, timeout-guarded wrapper around the ``tailscale`` CLI.

Every method degrades gracefully when Tailscale is missing or not running so the
app remains fully usable on a LAN.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_TIMEOUT = 5.0

# Background services (launchd on macOS, systemd) run with a minimal PATH that
# often excludes Homebrew and the Tailscale.app bundle, so `which tailscale`
# fails there even when the CLI is installed. Check these absolute paths too.
_KNOWN_BINARIES = (
    "/opt/homebrew/bin/tailscale",  # Apple Silicon Homebrew
    "/usr/local/bin/tailscale",  # Intel Homebrew / common Linux
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",  # macOS App Store / standalone
    "/usr/bin/tailscale",  # Linux package
    "/usr/sbin/tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",  # Windows
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",  # Windows (32-bit install)
)


@dataclass
class TailscaleStatus:
    installed: bool
    running: bool
    ipv4: str | None
    magic_dns: str | None  # e.g. "host.tailnet-name.ts.net"
    served: bool = False


@dataclass
class TailscalePeer:
    dns_name: str  # e.g. "tailcam-pi.tailnet-name.ts.net"
    ipv4: str | None
    online: bool


def _resolve_binary(name: str) -> str | None:
    """Find the tailscale CLI on PATH or in known absolute locations."""
    found = shutil.which(name)
    if found:
        return found
    for path in _KNOWN_BINARIES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class TailscaleClient:
    def __init__(self, binary: str = "tailscale") -> None:
        self._binary = _resolve_binary(binary)

    def _run(self, *args: str) -> subprocess.CompletedProcess | None:
        if self._binary is None:
            return None
        try:
            return subprocess.run(
                [self._binary, *args],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("tailscale %s failed: %s", " ".join(args), exc)
            return None

    def is_installed(self) -> bool:
        return self._binary is not None

    def status(self) -> TailscaleStatus:
        if not self.is_installed():
            return TailscaleStatus(False, False, None, None)
        proc = self._run("status", "--json")
        if proc is None or proc.returncode != 0 or not proc.stdout:
            return TailscaleStatus(True, False, None, None)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return TailscaleStatus(True, False, None, None)
        running = data.get("BackendState") == "Running"
        self_node = data.get("Self") or {}
        ips = self_node.get("TailscaleIPs") or []
        ipv4 = next((ip for ip in ips if ":" not in ip), None)
        magic = (self_node.get("DNSName") or "").rstrip(".") or None
        return TailscaleStatus(True, running, ipv4, magic)

    def peers(self) -> list[TailscalePeer]:
        """Return online tailnet peers (other devices), for TailCam discovery."""
        if not self.is_installed():
            return []
        proc = self._run("status", "--json")
        if proc is None or proc.returncode != 0 or not proc.stdout:
            return []
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        out: list[TailscalePeer] = []
        for node in (data.get("Peer") or {}).values():
            dns = (node.get("DNSName") or "").rstrip(".")
            if not dns:
                continue
            ips = node.get("TailscaleIPs") or []
            ipv4 = next((ip for ip in ips if ":" not in ip), None)
            out.append(TailscalePeer(dns_name=dns, ipv4=ipv4, online=bool(node.get("Online"))))
        return out

    def serve(self, local_port: int, https_port: int = 8443) -> bool:
        """Expose a local port over HTTPS within the tailnet (background).

        ``https_port`` is the tailnet-facing port. Using a non-443 port (e.g.
        8443) keeps TailCam off the root URL so it won't clobber another app
        already served at ``https://<host>/``.
        """
        proc = self._run(
            "serve", "--bg", f"--https={https_port}", f"localhost:{local_port}"
        )
        if proc is None:
            return False
        if proc.returncode != 0:
            log.warning("tailscale serve failed: %s", proc.stderr.strip())
            return False
        return True

    def serve_off(self, https_port: int) -> bool:
        """Remove only TailCam's handler on ``https_port`` (leaves others intact)."""
        proc = self._run("serve", "--https", str(https_port), "off")
        return proc is not None and proc.returncode == 0

    def serve_reset(self) -> bool:
        proc = self._run("serve", "reset")
        return proc is not None and proc.returncode == 0

    def access_url(self, local_port: int, served: bool, https_port: int = 8443) -> str:
        status = self.status()
        if served and status.magic_dns:
            if https_port == 443:
                return f"https://{status.magic_dns}/"
            return f"https://{status.magic_dns}:{https_port}/"
        if status.ipv4:
            return f"http://{status.ipv4}:{local_port}/"
        return f"http://localhost:{local_port}/"
