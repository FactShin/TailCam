from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tailcam import __version__
from tailcam.web.app import create_app


@pytest.fixture
def local_client(context):
    app = create_app(context.config, context=context)
    with TestClient(
        app,
        base_url="http://localhost:8088",
        client=("127.0.0.1", 50000),
    ) as test_client:
        yield test_client


def test_node_capabilities_and_health_contract(local_client) -> None:
    caps = local_client.get("/api/v1/node/capabilities")
    health = local_client.get("/api/v1/node/health")

    assert caps.status_code == 200
    assert caps.json()["api_version"] == "1"
    assert "node.reload" in caps.json()["capabilities"]
    assert caps.json()["principal"]["verified"] is True

    assert health.status_code == 200
    body = health.json()
    assert body["version"] == __version__
    assert body["camera_total"] >= 1
    assert isinstance(body["issues"], list)


def test_node_audit_pagination(local_client) -> None:
    assert local_client.post("/api/v1/node/actions/reload").status_code == 200
    assert local_client.post("/api/v1/node/actions/reload").status_code == 200

    first = local_client.get("/api/v1/node/audit?limit=1&offset=0")
    second = local_client.get("/api/v1/node/audit?limit=1&offset=1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(first.json()) == 1
    assert len(second.json()) == 1
    assert first.json()[0]["id"] > second.json()[0]["id"]
    assert first.json()[0]["action"] == "node.reload"


def test_local_loopback_reload_succeeds_and_audits(local_client) -> None:
    resp = local_client.post("/api/v1/node/actions/reload")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "node.reload"
    assert body["result"] == "success"
    assert body["health"]["camera_total"] >= 1

    audit = local_client.get("/api/v1/node/audit").json()
    assert audit[0]["actor"] == "local"
    assert audit[0]["source"] == "local"
    assert audit[0]["metadata"]["camera_count"] >= 1


def test_verified_tailnet_personal_reload_succeeds(context) -> None:
    app = create_app(context.config, context=context)
    with TestClient(
        app,
        base_url="https://tailcam.example.ts.net:8443",
        client=("127.0.0.1", 50000),
        headers={"Tailscale-User-Login": "alice@example.com"},
    ) as test_client:
        resp = test_client.post("/api/v1/node/actions/reload")

    assert resp.status_code == 200, resp.text


def test_unverified_node_mutation_returns_403(context) -> None:
    app = create_app(context.config, context=context)
    with TestClient(
        app,
        base_url="https://tailcam.example.ts.net:8443",
        client=("100.64.0.22", 50000),
    ) as test_client:
        resp = test_client.post("/api/v1/node/actions/reload")

    assert resp.status_code == 403


def test_existing_api_behavior_remains_unchanged(local_client) -> None:
    assert local_client.get("/api/system").status_code == 200
    assert local_client.get("/api/cameras").status_code == 200
