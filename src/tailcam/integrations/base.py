"""Shared helpers for the home-automation integrations.

Both HomeKit and Home Assistant reuse TailCam's existing, unauthenticated
streaming endpoints (Tailscale is the security boundary):

- live MJPEG : ``GET /stream/<camera_id>.mjpg``
- snapshot   : ``GET /stream/<camera_id>/snapshot.jpg``
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from tailcam.web.context import AppContext


@dataclass
class CameraRef:
    id: str
    name: str
    slug: str  # safe id for MQTT topics / object ids


def slugify(value: str) -> str:
    """A topic/object-id-safe slug (lowercase, ``a-z0-9_``)."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return slug or "cam"


def local_ip() -> str:
    """Best-guess primary LAN IPv4 of this host (no packets are sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def selected_cameras(ctx: AppContext, ids: list[str]) -> list[CameraRef]:
    """Resolve the configured camera ids (empty = all) to ``CameraRef``s,
    preserving discovery order and de-duplicating slugs."""
    wanted = set(ids)
    out: list[CameraRef] = []
    used: set[str] = set()
    for cam in ctx.manager.list():
        cid = cam.descriptor.id
        if wanted and cid not in wanted:
            continue
        slug = slugify(cid)
        base = slug
        i = 2
        while slug in used:
            slug = f"{base}_{i}"
            i += 1
        used.add(slug)
        out.append(CameraRef(id=cid, name=cam.name or cid, slug=slug))
    return out


def _stream_path(camera_id: str) -> str:
    return f"/stream/{quote(camera_id, safe='/')}.mjpg"


def _snapshot_path(camera_id: str) -> str:
    return f"/stream/{quote(camera_id, safe='/')}/snapshot.jpg"


def local_base_url(ctx: AppContext) -> str:
    """Loopback base URL for same-host fetches (HomeKit snapshot/ffmpeg source)."""
    return f"http://127.0.0.1:{ctx.config.server.port}"


def public_base_url(ctx: AppContext) -> str:
    """Base URL another machine (e.g. Home Assistant) should use to reach
    TailCam — the Tailscale host, https when Tailscale Serve is active."""
    host = ctx.local_host or local_ip()
    if getattr(ctx, "served", False):
        port = ctx.config.tailscale.serve_port
        return f"https://{host}" if port == 443 else f"https://{host}:{port}"
    return f"http://{host}:{ctx.config.server.port}"


def mjpeg_url(base: str, camera_id: str) -> str:
    return base + _stream_path(camera_id)


def snapshot_url(base: str, camera_id: str) -> str:
    return base + _snapshot_path(camera_id)
