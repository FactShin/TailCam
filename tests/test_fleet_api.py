from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tailcam.cluster.service import Peer
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


def test_fleet_local_health_and_reload(local_client) -> None:
    health = local_client.get("/api/v1/fleet/nodes/local/health")
    reload = local_client.post("/api/v1/fleet/nodes/local/actions/reload")

    assert health.status_code == 200
    assert health.json()["camera_total"] >= 1
    assert reload.status_code == 200
    assert reload.json()["action"] == "node.reload"


def test_fleet_relay_rejects_unknown_node(local_client) -> None:
    resp = local_client.get("/api/v1/fleet/nodes/nope/health")

    assert resp.status_code == 404


def test_hosts_include_fleet_node_keys(context) -> None:
    context.cluster._peers = [
        Peer(
            key="peer-node",
            host="peer-node.example.ts.net",
            base_url="https://peer-node.example.ts.net:8443",
            online=True,
            version="0.90.0",
            camera_count=2,
        )
    ]
    context.cluster._by_key = {"peer-node": context.cluster._peers[0]}
    context.cluster._fetched_at = time.monotonic()

    app = create_app(context.config, context=context)
    with TestClient(app, base_url="http://localhost:8088", client=("127.0.0.1", 50000)) as c:
        hosts = c.get("/api/hosts").json()

    assert hosts[0]["kind"] == "local"
    assert hosts[0]["node_key"] == "local"
    peer = next(host for host in hosts if host["kind"] == "peer")
    assert peer["node_key"] == "peer-node"
    assert peer["proxy_prefix"] == "/proxy/peer-node"


def test_remote_fleet_reload_dispatches_only_allowlisted_path(context) -> None:
    seen: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.headers)))
        if request.url.path == "/api/v1/node/actions/reload":
            return httpx.Response(
                200,
                json={
                    "action": "node.reload",
                    "target": "peer-node",
                    "result": "success",
                    "detail": "capture workers reloaded",
                    "health": _remote_health(),
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    context.cluster._peers = [
        Peer(
            key="peer-node",
            host="peer-node.example.ts.net",
            base_url="https://peer-node.example.ts.net:8443",
            online=True,
            version="0.90.0",
        )
    ]
    context.cluster._by_key = {"peer-node": context.cluster._peers[0]}
    context.cluster._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    app = create_app(context.config, context=context)
    with TestClient(
        app,
        base_url="http://localhost:8088",
        client=("127.0.0.1", 50000),
        headers={"Tailscale-User-Login": "alice@example.com"},
    ) as test_client:
        resp = test_client.post(
            "/api/v1/fleet/nodes/peer-node/actions/reload",
            headers={"Tailscale-App-Capabilities": '{"spoofed":true}'},
        )
        audit = test_client.get("/api/v1/node/audit").json()

    assert resp.status_code == 200, resp.text
    assert seen == [("POST", "/api/v1/node/actions/reload", seen[0][2])]
    assert "tailscale-app-capabilities" not in seen[0][2]
    assert audit[0]["action"] == "fleet.node.reload"
    assert audit[0]["target"] == "peer-node"


def test_generic_proxy_rejects_management_paths(context) -> None:
    context.cluster._peers = [
        Peer(
            key="peer-node",
            host="peer-node.example.ts.net",
            base_url="https://peer-node.example.ts.net:8443",
            online=True,
            version="0.90.0",
        )
    ]
    context.cluster._by_key = {"peer-node": context.cluster._peers[0]}

    app = create_app(context.config, context=context)
    with TestClient(app, base_url="http://localhost:8088", client=("127.0.0.1", 50000)) as c:
        node = c.get("/proxy/peer-node/api/v1/node/health")
        fleet = c.get("/proxy/peer-node/api/v1/fleet/nodes/local/health")

    assert node.status_code == 403
    assert fleet.status_code == 403


def _remote_health() -> dict:
    return {
        "host": "peer-node.example.ts.net",
        "version": "0.90.0",
        "platform": "Linux arm64",
        "python_version": "3.12.13",
        "uptime_seconds": 5.0,
        "tailscale_installed": True,
        "tailscale_running": True,
        "tailscale_served": True,
        "access_url": "https://peer-node.example.ts.net:8443/",
        "local_url": "http://localhost:8088/",
        "camera_total": 1,
        "camera_online": 1,
        "camera_offline": 0,
        "camera_degraded": 0,
        "camera_recording": 0,
        "media_bytes": 0,
        "timelapse_bytes": 0,
        "update_current": "0.90.0",
        "update_latest": "0.90.0",
        "update_available": False,
        "ai_enabled": False,
        "ai_reachable": False,
        "ai_model": "moondream",
        "ai_model_present": False,
        "issues": [],
    }
