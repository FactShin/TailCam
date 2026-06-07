"""Peer discovery, camera aggregation, and reverse-proxy routing.

A node becomes an *aggregator*: it discovers other AnyCam nodes on the tailnet
(via Tailscale peers + a static config list), asks each for its **local-only**
camera list, merges them, and proxies remote streams/actions so the browser only
ever talks to the node it opened.

To avoid recursive fan-out, peers are always queried with ``?scope=local``.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import time
from dataclasses import dataclass

import httpx

from anycam.config import PeersConfig
from anycam.logging_setup import get_logger
from anycam.tailscale.client import TailscaleClient

log = get_logger(__name__)

_PROBE_TIMEOUT = 2.5
_FETCH_TIMEOUT = 3.0
_TTL_SECONDS = 20.0


@dataclass
class Peer:
    key: str  # url-safe id used in /proxy/{key}/... (short hostname label)
    host: str  # display hostname (full MagicDNS name or URL host)
    base_url: str  # no trailing slash
    online: bool = False
    version: str | None = None
    camera_count: int = 0


def resolve_local_host(tailscale: TailscaleClient) -> str:
    """This node's identity — ANYCAM_HOST override, else MagicDNS name, else hostname."""
    override = os.environ.get("ANYCAM_HOST")
    if override:
        return override
    try:
        st = tailscale.status()
        if st.magic_dns:
            return st.magic_dns
    except Exception:  # pragma: no cover - defensive
        pass
    return socket.gethostname()


def _key_for(host: str) -> str:
    label = host.split(".")[0].lower()
    return re.sub(r"[^a-z0-9-]", "-", label) or "peer"


def _env_static_peers() -> list[str]:
    raw = os.environ.get("ANYCAM_PEERS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


class ClusterService:
    """Owns peer discovery, the shared HTTP client, and aggregation."""

    def __init__(
        self,
        config: PeersConfig,
        tailscale: TailscaleClient,
        local_host: str,
        serve_port: int = 8443,
    ) -> None:
        self._config = config
        self._tailscale = tailscale
        self.local_host = local_host
        self._serve_port = serve_port
        self._client: httpx.AsyncClient | None = None
        self._peers: list[Peer] = []
        self._by_key: dict[str, Peer] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            # read=None: MJPEG proxy streams are open-ended.
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, read=None), follow_redirects=True
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- discovery ---------------------------------------------------------
    def _candidate_urls(self) -> list[str]:
        urls: list[str] = list(self._config.static) + _env_static_peers()
        if self._config.auto_discover:
            try:
                for peer in self._tailscale.peers():
                    if peer.online:
                        urls.append(f"https://{peer.dns_name}:{self._serve_port}")
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("tailscale peer discovery failed: %s", exc)
        # de-dupe, strip trailing slashes
        seen, out = set(), []
        for u in urls:
            u = u.rstrip("/")
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    async def _probe(self, base_url: str) -> Peer | None:
        try:
            r = await self.client().get(f"{base_url}/api/system", timeout=_PROBE_TIMEOUT)
            if r.status_code != 200:
                return None
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return None
        host = data.get("host") or httpx.URL(base_url).host or base_url
        if host == self.local_host:
            return None  # that's us
        return Peer(
            key=_key_for(host),
            host=host,
            base_url=base_url,
            online=True,
            version=data.get("version"),
        )

    async def refresh(self, force: bool = False) -> list[Peer]:
        if not force and (time.monotonic() - self._fetched_at) < _TTL_SECONDS:
            return self._peers
        async with self._lock:
            if not force and (time.monotonic() - self._fetched_at) < _TTL_SECONDS:
                return self._peers
            candidates = self._candidate_urls()
            probed = await asyncio.gather(*(self._probe(u) for u in candidates))
            peers: dict[str, Peer] = {}
            for p in probed:
                if p is not None:
                    peers[p.key] = p  # de-dupe by key
            self._peers = list(peers.values())
            self._by_key = peers
            self._fetched_at = time.monotonic()
            return self._peers

    async def peers(self) -> list[Peer]:
        return await self.refresh()

    def peer_base(self, key: str) -> str | None:
        peer = self._by_key.get(key)
        return peer.base_url if peer else None

    # -- aggregation -------------------------------------------------------
    async def _remote_items(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch a list endpoint from every peer (scope=local) and tag each item
        with the peer's host + proxy_prefix. Used for cameras, media, events."""
        peers = await self.peers()
        if not peers:
            return []
        query = {**(params or {}), "scope": "local"}

        async def fetch(peer: Peer) -> list[dict]:
            try:
                r = await self.client().get(
                    f"{peer.base_url}{path}", params=query, timeout=_FETCH_TIMEOUT
                )
                r.raise_for_status()
                items = r.json()
            except (httpx.HTTPError, ValueError):
                peer.online = False
                return []
            prefix = f"/proxy/{peer.key}"
            for it in items:
                it["host"] = peer.host
                it["proxy_prefix"] = prefix
            if path == "/api/cameras":
                peer.camera_count = len(items)
            return items

        chunks = await asyncio.gather(*(fetch(p) for p in peers))
        return [it for chunk in chunks for it in chunk]

    async def remote_cameras(self) -> list[dict]:
        return await self._remote_items("/api/cameras")

    async def remote_media(self, params: dict) -> list[dict]:
        return await self._remote_items("/api/media", params)

    async def remote_events(self, params: dict) -> list[dict]:
        return await self._remote_items("/api/events", params)
