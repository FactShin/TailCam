"""Remote start must not become a second capture on the camera node."""

import httpx
import pytest


def _remote(context, monkeypatch, handler):
    context.config.storage.node = "storage-box"
    monkeypatch.setattr(context, "resolve_node_base", lambda _: "http://storage-box:8088")
    context.capture._client = httpx.Client(transport=httpx.MockTransport(handler))
    return context.manager.list()[0].descriptor.id


@pytest.mark.parametrize("status", [400, 403, 404, 409, 422, 500, 502, 503])
def test_remote_rejection_never_creates_local_capture(client, context, monkeypatch, status):
    cam = _remote(context, monkeypatch, lambda _: httpx.Response(
        status, json={"detail": "printer analysis disabled on storage"},
    ))
    for _ in range(2):
        response = client.post(f"/api/cameras/{cam}/timelapse/start")
        assert response.status_code == status
        assert response.json()["detail"] == "printer analysis disabled on storage"
        assert context.store.list_timelapses() == []
        assert context.capture.target() is not None  # rejection is not an outage


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError])
def test_uncertain_remote_start_never_falls_back(client, context, monkeypatch, error):
    def fail(request):
        raise error("reply lost after send", request=request)

    cam = _remote(context, monkeypatch, fail)
    response = client.post(f"/api/cameras/{cam}/timelapse/start")
    assert response.status_code in (502, 504)
    assert "may have started" in response.json()["detail"]
    assert context.store.list_timelapses() == []
    assert context.capture.target() is not None


@pytest.mark.parametrize("payload", [[], None, {"id": 1}, {"id": "wrong"}])
def test_invalid_remote_success_never_falls_back(client, context, monkeypatch, payload):
    cam = _remote(context, monkeypatch, lambda _: httpx.Response(200, json=payload))
    response = client.post(f"/api/cameras/{cam}/timelapse/start")
    assert response.status_code == 502
    assert context.store.list_timelapses() == []


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout])
def test_connection_failure_keeps_documented_local_fallback(client, context, monkeypatch, error):
    def fail(request):
        raise error("connection unavailable", request=request)

    cam = _remote(context, monkeypatch, fail)
    response = client.post(
        f"/api/cameras/{cam}/timelapse/start", json={"analysis_enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["host"] == context.local_host
    assert response.json()["proxy_prefix"] == ""
    assert len(context.store.list_timelapses()) == 1


def test_fallback_rechecks_local_analyzer(client, context, monkeypatch):
    def fail(request):
        raise httpx.ConnectError("connection unavailable", request=request)

    cam = _remote(context, monkeypatch, fail)
    response = client.post(
        f"/api/cameras/{cam}/timelapse/start", json={"analysis_enabled": True},
    )
    assert response.status_code == 409
    assert context.store.list_timelapses() == []


def test_remote_endpoint_checks_effective_analysis_default(client, context, monkeypatch):
    context.config.timelapse.analysis_enabled = True
    monkeypatch.setattr(context.cluster, "peer_base", lambda _: "http://source:8088")
    monkeypatch.setattr(context.remote_feeds, "get_buffer", lambda *args: pytest.fail(
        "should reject before opening a stream",
    ))
    response = client.post("/api/remote/source/cameras/synthetic-0/timelapse/start")
    # Send a body because the remote endpoint requires one.
    assert response.status_code == 422
    response = client.post("/api/remote/source/cameras/synthetic-0/timelapse/start", json={})
    assert response.status_code == 409
    assert context.store.list_timelapses() == []


@pytest.mark.parametrize("connected", [False, True])
def test_url_destination_never_becomes_a_proxy_key(client, context, monkeypatch, connected):
    async def no_identity(*args, **kwargs):
        return []

    def response(request):
        if not connected:
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(200, json={"id": 1})

    cam = _remote(context, monkeypatch, response)
    context.config.storage.node = "http://storage-box:8088"
    monkeypatch.setattr(context.cluster, "refresh", no_identity)
    result = client.post(f"/api/cameras/{cam}/timelapse/start")
    if connected:
        assert result.status_code == 502
        assert "identity" in result.json()["detail"]
        assert context.store.list_timelapses() == []
    else:
        assert result.status_code == 200
        assert result.json()["proxy_prefix"] == ""
        assert result.json()["host"] == context.local_host
