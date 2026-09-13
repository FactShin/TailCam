"""Public workload trust boundaries and truthful legacy inference compatibility."""

from __future__ import annotations

import hashlib
import json
import struct
import time
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tailcam.cluster.service import Peer
from tailcam.persistence.models import ModelRecord
from tailcam.storage.models import Artifact, DestinationRef
from tailcam.streaming.image_validation import raster_dimensions
from tailcam.tailscale.client import TAILCAM_APP_CAPABILITY
from tailcam.training.inference import InferenceRouter
from tailcam.web.app import create_app


@pytest.fixture
def http(context):
    app = create_app(context.config, context=context)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 51000)) as client:
        yield client


def jpeg():
    ok, value = cv2.imencode(".jpg", np.zeros((24, 32, 3), dtype=np.uint8))
    assert ok
    return value.tobytes()


def test_incoming_legacy_inference_never_calls_source_router(http, context, monkeypatch):
    monkeypatch.setattr(InferenceRouter, "detection_active", property(lambda self: True))
    monkeypatch.setattr(context.inference, "detect", lambda *a, **kw: pytest.fail("rerouted"))
    monkeypatch.setattr(context.inference, "detect_local", lambda image: [])
    response = http.post("/api/detect-image", content=jpeg())
    assert response.status_code == 200
    assert response.json()["detector_active"] is True
    assert response.json()["boxes"] == []
    monkeypatch.setattr(context.inference, "detect_local", lambda image: None)
    response = http.post("/api/detect-image", content=jpeg())
    assert response.json()["detector_active"] is False
    assert response.json()["boxes"] == []


def test_image_dimensions_checked_before_native_decode(http, monkeypatch):
    monkeypatch.setattr(cv2, "imdecode", lambda *a, **kw: pytest.fail("oversized image decoded"))
    oversized = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 100000, 100000) + b"\0" * 9
    )
    assert http.post("/api/detect-image", content=oversized).status_code == 400
    assert http.post("/api/detect-image", content=b"invalid").status_code == 400
    assert (
        http.post(
            "/api/detect-image",
            content=b"x",
            headers={
                "content-length": str(13 * 1024**2),
            },
        ).status_code
        == 413
    )


def test_normal_raster_headers_and_conflicting_jpeg_dimensions():
    data = jpeg()
    assert raster_dimensions(data) == (32, 24)
    ok, png = cv2.imencode(".png", np.zeros((24, 32, 3), dtype=np.uint8))
    assert ok and raster_dimensions(png.tobytes()) == (32, 24)
    # A second contradictory SOF is rejected before native decompression.
    inserted = b"\xff\xc0\x00\x08\x08\x00\x10\x00\x10\x01"
    with pytest.raises(ValueError):
        raster_dimensions(data[:2] + inserted + data[2:])


def test_viewer_cannot_spend_worker_budget_or_change_placement(context):
    app = create_app(context.config, context=context)
    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 51000),
        headers={
            "tailscale-user-login": "viewer@example.test",
            "tailscale-app-capabilities": json.dumps(
                {TAILCAM_APP_CAPABILITY: [{"roles": ["viewer"]}]}
            ),
        },
    ) as client:
        assert client.get("/api/v1/workloads/policy").status_code == 200
        assert client.get("/api/v1/workloads/worker").status_code == 200
        assert client.post("/api/detect-image", content=jpeg()).status_code == 403
        assert client.post("/api/v1/jobs", json={}).status_code == 403
        assert client.post("/api/v1/workloads/live/execute", json={}).status_code == 403
        assert client.patch("/api/v1/workloads/policy", json={}).status_code == 403


def test_placement_revision_and_bounded_nonreflecting_body(http, context):
    original = http.get("/api/v1/workloads/policy").json()
    body = {"expected_revision": original["policy"]["revision"], "policy": original["policy"]}
    assert http.patch("/api/v1/workloads/policy", json=body).status_code == 200
    assert http.patch("/api/v1/workloads/policy", json=body).status_code == 409
    rejected = http.post("/api/v1/jobs", json={"private_path": "/home/private/secret"})
    assert rejected.status_code == 422
    assert "secret" not in rejected.text
    invalid_provider = http.post(
        "/api/v1/workloads/providers",
        json={
            "provider_id": "private/path",
            "name": "Invalid identity",
            "base_url": "http://models.test:11434",
            "model": "vision",
        },
    )
    assert invalid_provider.status_code == 422
    assert "private/path" not in invalid_provider.text
    assert (
        http.post(
            "/api/v1/jobs",
            content=b"x",
            headers={
                "content-length": str(2 * 1024**2),
            },
        ).status_code
        == 413
    )


def test_training_page_inventory_does_not_initialize_engine(http, monkeypatch):
    from tailcam.training import engine

    monkeypatch.setattr(engine, "engine_info", lambda: pytest.fail("native runtime probed in HTTP"))
    monkeypatch.setattr(engine, "version", lambda name: "test-installed-metadata")
    response = http.get("/api/training")
    assert response.status_code == 200
    assert response.json()["engine_available"] is True
    assert response.json()["device"] == "unchecked"


def test_remote_owned_model_remains_selectable_without_local_path(http, context):
    owner = str(uuid4())
    artifact = Artifact(
        artifact_id=str(uuid4()),
        owner_node_id=owner,
        origin_node_id=context.node_id,
        kind="model_output",
        size_bytes=0,
        sha256=hashlib.sha256(b"").hexdigest(),
        created_at=1,
        updated_at=1,
        policy_revision=1,
        requested_destination=DestinationRef(node_id=owner),
    )
    context.storage_service.catalog.save(artifact)
    model_id = context.store.add_model(
        ModelRecord(
            id=None,
            name="Remote trained model",
            kind="trained",
            path="",
            classes_json='["ok"]',
            base_model="model:1",
            metrics_json="{}",
            created_ts=1,
        )
    )
    context.storage_service.catalog.alias("model", str(model_id), "file", artifact.artifact_id)
    model = next(item for item in http.get("/api/models").json() if item["id"] == model_id)
    assert model["has_artifact"] is True
    assert model["artifact_owner_node_id"] == owner
    assert model["registry_node_id"] == context.node_id


def test_legacy_worker_names_use_only_unambiguous_approved_bound_ids(context, monkeypatch):
    directory = context.storage_peers
    identity = str(uuid4())
    base = "http://worker.test:8088"
    peer = Peer(key="gpu-box", host="worker.test", base_url=base)
    monkeypatch.setattr(directory, "_approved_peers", lambda: [peer])
    rows = [{"node_id": identity, "base": base, "online": False}]
    monkeypatch.setattr(directory, "refresh", lambda: rows)
    context.storage_service.catalog.set_setting(f"storage_peer_identity:{identity}", base)
    for name in ("gpu-box", "worker.test", base + "/", identity):
        assert directory.identity_for_legacy_node(name) == identity
    assert directory.identity_for_legacy_node("http://unapproved.test") is None
    rows.append({"node_id": str(uuid4()), "base": base, "online": True})
    context.storage_service.catalog.set_setting(
        f"storage_peer_identity:{rows[-1]['node_id']}", base
    )
    assert directory.identity_for_legacy_node("gpu-box") is None
    monkeypatch.setattr(directory, "_approved_peers", lambda: [])
    assert directory.identity_for_legacy_node(identity) is None


def test_job_retention_pin_http_contract_and_coordinator_boundary(http, context, tmp_path):
    storage = context.storage_service
    storage.register_location(str(tmp_path / "pin-media"), create=True)
    artifact = storage.put_bytes("snapshot", b"job input")
    path = f"/api/v1/artifacts/{artifact.artifact_id}/pins"
    pin = {
        "pin_id": str(uuid4()),
        "coordinator_node_id": context.node_id,
        "expires_at": time.time() + 120,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }
    response = http.post(path, json=pin)
    assert response.status_code == 200, response.text
    assert response.json()["pin_id"] == pin["pin_id"]
    assert http.post(path, json=pin).status_code == 200
    assert http.post(path, json={**pin, "coordinator_node_id": str(uuid4())}).status_code == 403
    assert http.post(path, json={**pin, "expires_at": "private-invalid-input"}).status_code == 422
    released = http.delete(
        path + "/" + pin["pin_id"], params={"coordinator_node_id": context.node_id}
    )
    assert released.status_code == 200 and released.json() == {"released": True}
    assert storage.resolve(artifact.artifact_id).read_bytes() == b"job input"
