"""Synchronous fleet snapshot for the CLI (status / doctor).

Wraps the async ClusterService so commands can show every tailnet node, whether
each is reachable, its version, and camera counts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from tailcam import __version__
from tailcam.cluster.service import ClusterService, resolve_local_host
from tailcam.config import AppConfig
from tailcam.tailscale.client import TailscaleClient


@dataclass
class FleetNode:
    host: str
    role: str  # "local" | "peer"
    reachable: bool
    version: str | None
    camera_count: int


@dataclass
class Fleet:
    local_host: str
    nodes: list[FleetNode] = field(default_factory=list)
    remote_cameras: list[dict] = field(default_factory=list)


def gather_fleet(config: AppConfig, local_camera_count: int) -> Fleet:
    ts = TailscaleClient()
    local_host = resolve_local_host(ts)
    svc = ClusterService(config.peers, ts, local_host, config.tailscale.serve_port)

    async def go() -> tuple[list, list[dict]]:
        peers = await svc.peers()
        remote = await svc.remote_cameras()
        await svc.aclose()
        return peers, remote

    try:
        peers, remote = asyncio.run(go())
    except Exception:
        peers, remote = [], []

    counts: dict[str, int] = {}
    for cam in remote:
        counts[cam.get("host", "?")] = counts.get(cam.get("host", "?"), 0) + 1

    nodes = [FleetNode(local_host, "local", True, __version__, local_camera_count)]
    for p in peers:
        nodes.append(FleetNode(p.host, "peer", p.online, p.version, counts.get(p.host, 0)))
    return Fleet(local_host=local_host, nodes=nodes, remote_cameras=remote)
