"""Identity resolution and cached catalog trust without real fleet services."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from tailcam.storage.models import Artifact, DestinationRef
from tailcam.web import storage_peers
from tailcam.web.storage_peers import StoragePeerDirectory


def _directory(context, bases):
    context.config.peers.static = bases
    return StoragePeerDirectory(context)


def _http(monkeypatch, handler):
    client_type = httpx.Client
    monkeypatch.setattr(
        storage_peers.httpx,
        "Client",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )


def test_explicit_peer_resolves_without_dashboard_discovery(context, monkeypatch):
    peer_id = str(uuid4())
    directory = _directory(context, ["http://owner.test"])

    def handler(request):
        if request.url.path == "/api/v1/node/config":
            return httpx.Response(200, json={"node_id": peer_id})
        return httpx.Response(200, json={"items": []})

    _http(monkeypatch, handler)
    assert context.cluster.cached_peers() == []
    assert directory.resolve(peer_id) == "http://owner.test"
    context.config.peers.static = []
    assert directory.resolve(peer_id) is None


def test_conflicting_identity_claims_are_unavailable(context, monkeypatch):
    peer_id = str(uuid4())
    directory = _directory(context, ["http://one.test", "http://two.test"])
    _http(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=({"node_id": peer_id} if request.url.path.endswith("/config") else {"items": []}),
        ),
    )
    assert directory.resolve(peer_id) is None
    assert not directory.refresh()[0]["supported"]


def test_offline_identity_cannot_be_rebound_even_after_restart(context, monkeypatch):
    peer_id = str(uuid4())
    directory = _directory(context, ["http://original.test"])
    _http(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=({"node_id": peer_id} if request.url.path.endswith("/config") else {"items": []}),
        ),
    )
    assert directory.resolve(peer_id) == "http://original.test"
    restarted = _directory(context, ["http://replacement.test"])
    assert restarted.resolve(peer_id) is None
    restarted.reset_identity(peer_id)
    assert restarted.resolve(peer_id) == "http://replacement.test"


def test_compressed_peer_json_rejected_before_decoding(monkeypatch):
    import gzip

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=gzip.compress(b" " * 1000000),
            )
        )
    ) as client:
        with pytest.raises(ValueError, match="Compressed"):
            storage_peers._json(client, "http://owner.test", 65536)


def test_legacy_peer_is_visible_without_becoming_a_destination(context, monkeypatch):
    directory = _directory(context, ["http://old.test"])
    _http(monkeypatch, lambda request: httpx.Response(404))
    items = directory.destinations()
    assert len(items) == 2
    assert items[1]["node_id"] is None and items[1]["supported"] is False
    assert "base" not in items[1]


def test_chunked_json_limit_prevents_unbounded_peer_response():
    class Chunks(httpx.SyncByteStream):
        count = 0

        def __iter__(self):
            for _ in range(100):
                self.count += 1
                yield b" " * 65536

    stream = Chunks()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream),
        )
    ) as client:
        with pytest.raises(ValueError):
            storage_peers._json(client, "http://owner.test", 65536)
    assert stream.count == 2


def test_invalid_peer_catalog_keeps_old_entry_and_cursor(context, monkeypatch):
    peer_id = str(uuid4())
    destination = DestinationRef(node_id=peer_id)
    artifact = Artifact(
        artifact_id=str(uuid4()),
        owner_node_id=peer_id,
        origin_node_id=str(uuid4()),
        kind="snapshot",
        size_bytes=1,
        sha256="a" * 64,
        created_at=1,
        updated_at=1,
        requested_destination=destination,
        policy_revision=1,
    )
    catalog = context.storage_service.catalog
    catalog.import_index(peer_id, [artifact.model_dump(mode="json")])
    key = f"peer_catalog_cursor:{peer_id}"
    catalog.set_setting(key, "3")
    directory = _directory(context, ["http://owner.test"])
    _http(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "artifacts": [
                    {
                        **artifact.model_dump(mode="json"),
                        "owner_node_id": str(uuid4()),
                        "sha256": "b" * 64,
                    }
                ],
                "cursor": 4,
            },
        ),
    )
    directory._refresh_owner(
        {"node_id": peer_id, "online": True, "supported": True, "base": "http://owner.test"}
    )
    assert catalog.setting(key) == "3"
    cached = catalog.get(artifact.artifact_id)
    assert cached.owner_node_id == peer_id and cached.owner_online is False


def test_peer_paths_and_credentials_are_not_accepted(context):
    directory = _directory(
        context,
        ["file:///tmp/media", "http://user:password@owner.test", "http://owner.test?token=private"],
    )
    assert directory._approved_peers() == []
    context.cluster._peers = [SimpleNamespace(base_url="http://existing.test", key="existing")]
    assert len(directory._approved_peers()) == 1


def test_empty_change_page_marks_cached_owner_recovered(context, monkeypatch):
    peer_id = str(uuid4())
    artifact = Artifact(
        artifact_id=str(uuid4()),
        owner_node_id=peer_id,
        origin_node_id=str(uuid4()),
        kind="snapshot",
        size_bytes=1,
        sha256="a" * 64,
        created_at=1,
        updated_at=1,
        requested_destination=DestinationRef(node_id=peer_id),
        policy_revision=1,
    )
    catalog = context.storage_service.catalog
    catalog.import_index(peer_id, [artifact.model_dump(mode="json")])
    catalog.mark_owner_offline(peer_id)
    changes_before = catalog.changes()
    directory = _directory(context, ["http://owner.test"])
    _http(monkeypatch, lambda request: httpx.Response(200, json={"artifacts": [], "cursor": 3}))
    directory._refresh_owner(
        {"node_id": peer_id, "online": True, "supported": True, "base": "http://owner.test"}
    )
    assert catalog.get(artifact.artifact_id).owner_online is True
    assert catalog.changes() == changes_before
