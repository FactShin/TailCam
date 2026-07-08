"""Fleet nodes for the tray's "Nodes" submenu, from GET /api/hosts."""

from __future__ import annotations

import httpx

from tailcam.desktop.state import Node

_TIMEOUT = 3.0


def _peer_dashboard_url(host: str, serve_port: int = 8443) -> str | None:
    """The URL a peer's own dashboard should be reachable at.

    Peers expose HTTPS via Tailscale Serve on their MagicDNS name. We can only
    construct that when the host looks like a DNS name (contains a dot); a bare
    hostname gives us nothing routable, and /api/v1 admin on a peer requires
    its Serve identity headers anyway — so those entries render disabled with
    a hint instead of a dead link.
    """
    host = (host or "").strip().rstrip(".")
    if "." not in host:
        return None
    return f"https://{host}:{serve_port}/"


def fetch_nodes(base_url: str, serve_port: int = 8443) -> list[Node]:
    """All fleet nodes, best-effort. Empty list when the node is down."""
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/hosts", timeout=_TIMEOUT)
        resp.raise_for_status()
        hosts = resp.json()
    except Exception:
        return []
    nodes: list[Node] = []
    for h in hosts:
        kind = str(h.get("kind") or "peer")
        nodes.append(
            Node(
                key=str(h.get("node_key") or h.get("host") or ""),
                host=str(h.get("host") or ""),
                kind=kind,
                online=bool(h.get("online", True)),
                camera_count=int(h.get("camera_count") or 0),
                version=str(h.get("version") or ""),
                url=base_url
                if kind == "local"
                else _peer_dashboard_url(str(h.get("host") or ""), serve_port),
            )
        )
    return nodes
