"""Storage HTTP trust boundaries and compatibility across physical moves."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from tailcam.storage.models import MAX_CHUNK_BYTES, Artifact, DestinationRef, TransferManifest
from tailcam.tailscale.client import TAILCAM_APP_CAPABILITY
from tailcam.web.app import create_app


@pytest.fixture
def storage_client(context, tmp_path):
    # Do not start camera/notification workers for protocol-only checks.
    context.storage_service.register_location(str(tmp_path / "artifacts"), create=True)
    app = create_app(context.config, context=context)
    client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 51000))
    yield client
    client.close()


def _policy(client):
    return client.get("/api/v1/storage/policy").json()


def _declaration(context, data):
    service = context.storage_service
    destination = DestinationRef(
        node_id=context.node_id,
        location_id=service.locations.get().location_id,
    )
    artifact = Artifact(
        artifact_id=str(uuid4()),
        owner_node_id=context.node_id,
        origin_node_id=str(uuid4()),
        kind="snapshot",
        mime_type="image/jpeg",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        created_at=1,
        updated_at=1,
        requested_destination=destination,
        policy_revision=1,
    )
    return TransferManifest(
        artifact=artifact,
        destination=destination,
        idempotency_key=str(uuid4()),
    ).model_dump(mode="json")


def test_policy_revision_and_no_implicit_enable(storage_client):
    initial = _policy(storage_client)
    assert initial["enabled"] is False
    body = {"policy": initial["policy"], "expected_revision": initial["policy"]["revision"]}
    saved = storage_client.patch("/api/v1/storage/policy", json=body)
    assert saved.status_code == 200 and saved.json()["enabled"] is True
    assert saved.json()["policy"]["revision"] == body["expected_revision"] + 1
    conflict = storage_client.patch("/api/v1/storage/policy", json=body)
    assert conflict.status_code == 409
    assert _policy(storage_client) == saved.json()


def test_unverified_and_viewer_cannot_write(context):
    app = create_app(context.config, context=context)
    outside = TestClient(app, base_url="http://localhost", client=("192.0.2.9", 51000))
    assert outside.get("/api/v1/storage/policy").status_code == 403
    assert outside.post("/api/v1/transfers", json={}).status_code == 403
    viewer = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 51000),
        headers={
            "tailscale-user-login": "viewer@example.test",
            "tailscale-app-capabilities": json.dumps(
                {TAILCAM_APP_CAPABILITY: [{"roles": ["viewer"]}]}
            ),
        },
    )
    assert viewer.get("/api/v1/storage/policy").status_code == 200
    assert viewer.patch("/api/v1/storage/policy", json={}).status_code == 403
    assert viewer.post("/api/v1/transfers", json={}).status_code == 403


def test_upload_retry_range_checksum_and_manifest_limits(storage_client, context):
    data = b"abcdef"
    declaration = _declaration(context, data)
    opened = storage_client.post("/api/v1/transfers", json=declaration)
    assert opened.status_code == 200, opened.text
    transfer_id = opened.json()["transfer_id"]
    url = f"/api/v1/transfers/{transfer_id}"
    assert (
        storage_client.post("/api/v1/transfers", json=declaration).json()["transfer_id"]
        == transfer_id
    )
    chunk = b"abc"
    headers = {"X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()}
    assert (
        storage_client.put(url + "/chunks?offset=0", content=chunk, headers=headers).json()[
            "offset"
        ]
        == 3
    )
    assert (
        storage_client.put(url + "/chunks?offset=0", content=chunk, headers=headers).json()[
            "offset"
        ]
        == 3
    )
    assert storage_client.post(url + "/commit").status_code == 409
    assert (
        storage_client.put(
            url + "/chunks?offset=3", content=b"x" * (MAX_CHUNK_BYTES + 1)
        ).status_code
        == 413
    )
    chunk = b"def"
    headers["X-Chunk-SHA256"] = hashlib.sha256(chunk).hexdigest()
    assert (
        storage_client.put(url + "/chunks?offset=3", content=chunk, headers=headers).status_code
        == 200
    )
    committed = storage_client.post(url + "/commit")
    assert committed.status_code == 200, committed.text
    artifact_id = committed.json()["artifact_id"]
    assert storage_client.post(url + "/commit").json()["artifact_id"] == artifact_id
    response = storage_client.get(
        f"/api/v1/artifacts/{artifact_id}/content", headers={"Range": "bytes=1-3"}
    )
    assert response.status_code == 206 and response.content == b"bcd"
    assert response.headers["x-content-type-options"] == "nosniff"
    changes = storage_client.get("/api/v1/artifacts/changes").json()
    assert changes["artifacts"][0]["artifact_id"] == artifact_id
    assert "local_path" not in json.dumps(changes)
    assert (
        storage_client.post("/api/v1/transfers", content=b" " * (1024 * 1024 + 1)).status_code
        == 413
    )


def test_invalid_body_does_not_echo_private_values(storage_client):
    private = "/private/sensitive-user-file"
    response = storage_client.post("/api/v1/storage/admission", json={"kind": private})
    assert response.status_code == 422 and private not in response.text


@pytest.mark.parametrize(
    "namespace,legacy_id,variant,url",
    [
        ("media", 14, "file", "/media/14/file"),
        ("media", 14, "thumbnail", "/media/14/thumbnail"),
        ("motion", 19, "thumbnail", "/events/19/thumbnail"),
        ("sample", 21, "file", "/datasets/sample/21/image"),
        ("sample", 21, "thumbnail", "/datasets/sample/21/thumbnail"),
        ("timelapse", 9, "video", "/timelapse/9/file"),
        ("timelapse", 9, "smooth", "/timelapse/9/smooth"),
        ("timelapse", 9, "thumbnail", "/timelapse/9/thumbnail"),
        ("timelapse", 9, "frame/000003", "/timelapse/9/frame/3"),
    ],
)
def test_legacy_urls_resolve_before_missing_old_path(
    storage_client,
    context,
    tmp_path,
    namespace,
    legacy_id,
    variant,
    url,
):
    service = context.storage_service
    item = service.put_bytes("snapshot", b"stable image", mime_type="image/jpeg")
    service.catalog.alias(namespace, str(legacy_id), variant, item.artifact_id)
    original = service.resolve(item.artifact_id)
    target = service.register_location(str(tmp_path / "new-owner-root"), create=True)
    service.transfer_artifact(
        item.artifact_id,
        DestinationRef(
            node_id=context.node_id,
            location_id=target.location_id,
        ),
        remove_source=True,
    )
    assert not Path(original).exists()
    response = storage_client.get(url)
    assert response.status_code == 200 and response.content == b"stable image"


def test_transfer_and_content_do_not_follow_stale_owner_loops(storage_client, context):
    service = context.storage_service
    item = service.put_bytes("snapshot", b"cached")
    item.owner_node_id = str(uuid4())
    service.catalog.save(item)
    response = storage_client.get(
        f"/api/v1/artifacts/{item.artifact_id}/content",
        headers={"X-TailCam-Artifact-Hop": "1"},
    )
    assert response.status_code == 409
    response = storage_client.post(
        f"/api/v1/artifacts/{item.artifact_id}/transfer",
        json={
            "destination": {"node_id": context.node_id},
            "remove_source": True,
        },
    )
    assert response.status_code == 409


def test_disabled_storage_role_cannot_receive_or_edit_locations(storage_client, context, tmp_path):
    declaration = _declaration(context, b"x")
    location = context.storage_service.locations.get()
    context.active_roles = frozenset({"hub"})
    assert storage_client.post("/api/v1/transfers", json=declaration).status_code == 503
    response = storage_client.patch(
        f"/api/v1/storage/locations/{location.location_id}", json={"label": "changed"}
    )
    assert response.status_code == 503
    assert context.storage_service.locations.get().label != "changed"


@pytest.mark.parametrize(
    "path",
    [
        "api/v1/storage/policy",
        "api/v1/artifacts/00000000-0000-0000-0000-000000000001",
        "api/v1/transfers",
        "api%2Fv1%2Fstorage%2Fpolicy",
        "api//v1/storage/policy",
    ],
)
def test_generic_proxy_cannot_upgrade_storage_permissions(
    storage_client, context, monkeypatch, path
):
    def unexpected():
        raise AssertionError("Unauthorized request reached peer discovery")

    monkeypatch.setattr(context.cluster, "peers", unexpected)
    response = storage_client.patch(
        f"/proxy/owner/{path}",
        json={},
        headers={
            "tailscale-user-login": "viewer@example.test",
            "tailscale-app-capabilities": json.dumps(
                {TAILCAM_APP_CAPABILITY: [{"roles": ["viewer"]}]}
            ),
        },
    )
    assert response.status_code == 403


def test_retention_edit_protects_existing_artifact(storage_client, context):
    service = context.storage_service
    item = service.put_bytes("snapshot", b"keep this")
    response = storage_client.patch(
        f"/api/v1/artifacts/{item.artifact_id}/retention",
        json={
            "enabled": True,
            "max_age_seconds": 1,
            "min_replicas": 1,
            "protect": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["retention"]["protect"] is True
    assert storage_client.delete(f"/api/v1/artifacts/{item.artifact_id}").status_code == 409
    assert service.resolve(item.artifact_id).read_bytes() == b"keep this"


@pytest.mark.parametrize(
    "status,headers",
    [
        (200, {"Content-Length": "999"}),
        (200, {"Content-Length": "-1"}),
        (206, {"Content-Length": "2", "Content-Range": "bytes 0-1/999"}),
        (206, {"Content-Length": "2", "Content-Range": "bytes 3-4/5"}),
        (206, {"Content-Length": "2", "Content-Range": "bytes 0-1/*"}),
        (200, {"Content-Range": "bytes 0-1/5"}),
    ],
)
def test_remote_content_rejects_inconsistent_length_or_range(
    storage_client,
    context,
    monkeypatch,
    status,
    headers,
):
    from tailcam.web import routes_storage_v1

    peer_id = str(uuid4())
    item = Artifact(
        artifact_id=str(uuid4()),
        owner_node_id=peer_id,
        origin_node_id=context.node_id,
        kind="snapshot",
        size_bytes=5,
        sha256=hashlib.sha256(b"image").hexdigest(),
        created_at=1,
        updated_at=1,
        requested_destination=DestinationRef(node_id=peer_id),
        policy_revision=1,
    )
    context.storage_service.catalog.import_index(peer_id, [item.model_dump(mode="json")])
    monkeypatch.setattr(context.storage_peers, "resolve", lambda _: "http://owner.test")
    original = httpx.Client

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(status, content=b"image", headers=headers)

    monkeypatch.setattr(
        routes_storage_v1.httpx,
        "Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    response = storage_client.get(
        f"/api/v1/artifacts/{item.artifact_id}/content", headers={"Range": "bytes=0-1"}
    )
    assert response.status_code == 503
