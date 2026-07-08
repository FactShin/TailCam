"""Plain state the desktop shell renders. No GUI, no I/O — just data."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServerState:
    """What the menu needs to know about the node this shell fronts."""

    installed: bool = False  # service registered (POSIX; unreliable on Windows)
    running: bool = False  # /api/system answered — the source of truth
    port: int = 8088
    version: str = ""
    update_available: bool = False
    update_latest: str = ""
    # Client mode: this shell fronts a REMOTE node over its Tailscale Serve
    # URL — service lifecycle items are hidden (you can't launchctl a Mac
    # from someone else's laptop).
    client_mode: bool = False
    base_url: str = ""  # http://localhost:<port>/ or the remote --url

    @property
    def dashboard_url(self) -> str:
        return self.base_url or f"http://localhost:{self.port}/"


@dataclass
class Node:
    """One fleet node from /api/hosts, plus how to reach its dashboard."""

    key: str
    host: str
    kind: str  # "local" | "peer"
    online: bool = True
    camera_count: int = 0
    version: str = ""
    # The URL "Open <node>" navigates to. None = no reachable dashboard for
    # this peer (no Tailscale Serve HTTPS we can construct) — shown disabled.
    url: str | None = None


@dataclass
class MenuSpec:
    """One menu row in the pure model tray.py renders with pystray."""

    label: str
    action: str = ""  # action id dispatched to DesktopApp; "" = not clickable
    enabled: bool = True
    children: list[MenuSpec] = field(default_factory=list)
    separator: bool = False

    @staticmethod
    def sep() -> MenuSpec:
        return MenuSpec(label="-", separator=True)
