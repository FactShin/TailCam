"""Defensive checks at the proxy and remote-capture request boundaries."""

import httpx
import pytest
from fastapi import HTTPException

from tailcam.web.routes_proxy import _validate_proxy_path


@pytest.mark.parametrize("path", [
    "api/v1/node/health", "api%2Fv1%2Fnode/health",
    "x/../api/v1/node/health", "x/%2e%2e/api/v1/node/health",
    "x/%252e%252e/api/v1/node/health", "api/v1/fleet/nodes/local/health",
    "mcp", "mcp/", "proxy/other/api/v1/node/health", "api\\v1\\node/health",
])
def test_proxy_rejects_ambiguous_and_privileged_paths(path):
    with pytest.raises(HTTPException) as error:
        _validate_proxy_path(path)
    assert error.value.status_code in (400, 403)


@pytest.mark.parametrize("path", [
    "api/cameras", "api/cameras//dev/video0/timelapse/start",
    "stream//dev/video0.mjpg", "media/1/file", "api/timelapse/1/stop",
])
def test_proxy_keeps_camera_and_media_paths(path):
    _validate_proxy_path(path)


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_untrusted_host_rejected_before_read_or_write(client, method):
    response = client.request(method, "/api/system", headers={"host": "foreign.example"})
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["localhost:8088", "127.0.0.1:8088", "[::1]:8088",
                                       "camera.tailnet.ts.net:8443", "192.168.1.10:8088"])
def test_supported_read_hosts_remain_usable(client, host):
    assert client.get("/api/cameras?scope=local", headers={"host": host}).status_code == 200


def test_remote_timelapse_validates_camera_before_peer_lookup(client, context, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid camera must be rejected before network or stream access")

    monkeypatch.setattr(context.cluster, "peer_base", unexpected)
    monkeypatch.setattr(context.remote_feeds, "get_buffer", unexpected)
    response = client.post(
        "/api/remote/source/cameras/bad%3Fquery/timelapse/start", json={},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["", "   ", "unknown-node"])
def test_proxied_start_cannot_relabel_missing_owner_as_local(client, context, monkeypatch, host):
    async def peers():
        return []

    payload = {
        "id": 1, "camera_id": "synthetic-0", "name": "capture", "host": host,
        "state": "capturing", "mode": "interval", "interval_seconds": 1,
        "output_fps": 30, "frames_captured": 0, "created_ts": 1, "start_ts": 1,
    }
    context.cluster._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    monkeypatch.setattr(context.cluster, "peers", peers)
    monkeypatch.setattr(context.cluster, "peer_base", lambda _: "http://source:8088")
    result = client.post("/proxy/source/api/cameras/synthetic-0/timelapse/start", json={})
    assert result.status_code == 502
    assert "may have started" in result.json()["detail"]
