"""Storage identities resolved only through TailCam's configured peer registry."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from tailcam.cluster.service import Peer
from tailcam.storage.models import StorageError, StorageLocation

if TYPE_CHECKING:
    from tailcam.web.context import AppContext


def _json(client: httpx.Client, url: str, maximum: int, *, params=None):
    """Bound response bytes before decoding, including chunked peer responses."""
    with client.stream(
        "GET", url, params=params, headers={"Accept-Encoding": "identity"}
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("Compressed peer response is not accepted")
        body = bytearray()
        for chunk in response.iter_bytes(65536):
            if len(body) + len(chunk) > maximum:
                raise ValueError("Peer response exceeds protocol limit")
            body.extend(chunk)
        return json.loads(body)


class StoragePeerDirectory:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = threading.Lock()
        self._catalog_lock = threading.Lock()
        self._nodes: dict[str, dict] = {}
        self._refreshed = 0.0

    def _approved_peers(self) -> list[Peer]:
        peers = {p.base_url.rstrip("/"): p for p in self.ctx.cluster.cached_peers()}
        # Explicitly configured peers are useful before the first dashboard
        # poll and after restart. Automatic candidates retain the existing
        # opt-in discovery setting; selecting a storage owner still needs UUID.
        for raw in self.ctx.cluster._candidate_urls():
            base = raw.rstrip("/")
            try:
                parsed = urlsplit(base)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    continue
            except ValueError:
                continue
            if base not in peers:
                key = "configured-" + hashlib.sha256(base.encode()).hexdigest()[:12]
                peers[base] = Peer(key=key, host=parsed.hostname, base_url=base)
        return list(peers.values())[:32]

    def refresh(self, force: bool = False) -> list[dict]:
        with self._lock:
            if not force and time.monotonic() - self._refreshed < 15:
                return list(self._nodes.values())
            peers = self._approved_peers()
            allowed = {p.base_url.rstrip("/") for p in peers}
            previous = {
                key: {**item, "online": False}
                for key, item in self._nodes.items()
                if item["base"] in allowed
            }

            def inspect(peer):
                base = peer.base_url.rstrip("/")
                try:
                    with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
                        try:
                            config = _json(client, base + "/api/v1/node/config", 65536)
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code != 404:
                                raise
                            return {
                                "node_id": None,
                                "node_key": peer.key,
                                "node_name": peer.key,
                                "online": True,
                                "supported": False,
                                "base": base,
                                "locations": [],
                            }
                        node_id = str(UUID(config["node_id"]))
                        locations = []
                        try:
                            payload = _json(client, base + "/api/v1/storage/locations", 1024 * 1024)
                            supported = True
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code not in (403, 404, 503):
                                raise
                            supported = False
                        if supported:
                            rows = payload["items"]
                            if not isinstance(rows, list) or len(rows) > 100:
                                return None
                            locations = [StorageLocation.model_validate(x) for x in rows]
                            if any(x.node_id != node_id for x in locations):
                                return None
                        return {
                            "node_id": node_id,
                            "node_key": peer.key,
                            "node_name": str(config.get("name") or peer.key)[:128],
                            "online": True,
                            "supported": supported,
                            "base": base,
                            "locations": [x.model_dump(mode="json") for x in locations],
                        }
                except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
                    return None

            identities: dict[str, str] = {}
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="storage-peers") as pool:
                for result in pool.map(inspect, peers):
                    if result is not None:
                        identity = result["node_id"]
                        if identity == self.ctx.node_id:
                            continue
                        if (
                            identity
                            and identity in identities
                            and identities[identity] != result["base"]
                        ):
                            previous[identity]["online"] = False
                            previous[identity]["supported"] = False
                            continue
                        if identity:
                            binding_key = f"storage_peer_identity:{identity}"
                            bound = self.ctx.storage_service.catalog.setting(binding_key)
                            if bound and bound != result["base"]:
                                result.update(online=False, supported=False, identity_conflict=True)
                            elif not bound:
                                self.ctx.storage_service.catalog.set_setting(
                                    binding_key, result["base"]
                                )
                            identities[identity] = result["base"]
                        previous[identity or result["node_key"]] = result
            self._nodes = previous
            self._refreshed = time.monotonic()
            return list(previous.values())

    def resolve(self, node_id: str) -> str | None:
        node_id = str(UUID(node_id))
        # The registered base is checked again so removed peers lose access
        # immediately, even when identity information remains cached.
        allowed = {p.base_url.rstrip("/") for p in self._approved_peers()}
        for item in self.refresh():
            if item["node_id"] == node_id and item["online"] and item["base"] in allowed:
                return item["base"]
        return None

    def is_approved_identity(self, node_id: str) -> bool:
        """An offline binding can remain approved without claiming it is reachable."""
        identity = str(UUID(node_id))
        bound = self.ctx.storage_service.catalog.setting(f"storage_peer_identity:{identity}")
        return bool(bound and bound in {p.base_url.rstrip("/") for p in self._approved_peers()})

    def identity_for_legacy_node(self, name: str) -> str | None:
        """Resolve a saved key/address only to one approved, bound peer UUID."""
        peers = self._approved_peers()
        matching_bases = {
            peer.base_url.rstrip("/")
            for peer in peers
            if name.rstrip("/") in {peer.key, peer.host, peer.base_url.rstrip("/")}
        }
        identities = {
            item["node_id"]
            for item in self.refresh()
            if item["node_id"]
            and not item.get("identity_conflict")
            and (item["base"] in matching_bases or name == item["node_id"])
            and self.is_approved_identity(item["node_id"])
        }
        return next(iter(identities)) if len(identities) == 1 else None

    def identity_for_base(self, base: str) -> str | None:
        """A source UUID must come from the approved peer response, never its display name."""
        for item in self.refresh():
            if (
                item["base"] == base.rstrip("/")
                and item["online"]
                and item["node_id"]
                and not item.get("identity_conflict")
            ):
                return item["node_id"]
        return None

    def reset_identity(self, node_id: str) -> None:
        """Explicit administrator action after updating an approved peer address."""
        identity = str(UUID(node_id))
        with self._lock:
            self.ctx.storage_service.catalog.set_setting(f"storage_peer_identity:{identity}", "")
            self._nodes.pop(identity, None)
            self._refreshed = 0.0

    def destinations(self) -> list[dict]:
        items = [
            {
                "node_id": self.ctx.node_id,
                "node_key": "local",
                "node_name": self.ctx.config.node.name or self.ctx.local_host,
                "online": True,
                "supported": True,
                "locations": [
                    x.model_dump(mode="json") for x in self.ctx.storage_service.list_locations()
                ],
            }
        ]
        items.extend({k: v for k, v in x.items() if k != "base"} for x in self.refresh())
        return items

    def refresh_catalog(self) -> None:
        # Concurrent dashboard polls must not race durable peer cursors.
        if not self._catalog_lock.acquire(blocking=False):
            return
        try:
            peers = self.refresh()
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="storage-index") as pool:
                list(pool.map(self._refresh_owner, peers))
        finally:
            self._catalog_lock.release()

    def _refresh_owner(self, item: dict) -> None:
        node_id = item["node_id"]
        if node_id is None:
            return
        catalog = self.ctx.storage_service.catalog
        if not item["online"] or not item["supported"]:
            catalog.mark_owner_offline(node_id)
            return
        key = f"peer_catalog_cursor:{node_id}"
        cursor = int(catalog.setting(key) or "0")
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                data = _json(
                    client,
                    item["base"] + "/api/v1/artifacts/changes",
                    8 * 1024 * 1024,
                    params={"after": cursor, "limit": 100},
                )
            rows = data["artifacts"]
            next_cursor = int(data["cursor"])
            if not isinstance(rows, list) or len(rows) > 100 or next_cursor < cursor:
                raise ValueError("Invalid catalog page")
            # Core checks immutable identity and verified ownership handoffs;
            # an unavailable or invalid peer never clears the cached catalog.
            catalog.import_index(node_id, rows)
            catalog.mark_owner_online(node_id)
            catalog.set_setting(key, str(next_cursor))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError, StorageError):
            catalog.mark_owner_offline(node_id)
