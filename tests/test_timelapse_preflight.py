"""Execution-node checks must not depend on the dashboard's AI configuration."""

from __future__ import annotations

import httpx
import pytest

from tailcam.cluster.service import Peer
from tailcam.config import AIConfig
from tailcam.web.timelapse_preflight import display_endpoint


@pytest.fixture(autouse=True)
def capabilities_without_binaries_or_models(monkeypatch):
    from tailcam.timelapse import ffmpeg, rife
    from tailcam.timelapse.analyzer import PrinterAnalyzer

    monkeypatch.setattr(ffmpeg, "ffmpeg_source", lambda: "missing")
    monkeypatch.setattr(rife, "rife_available", lambda configured: False)

    def no_analysis(*args, **kwargs):
        pytest.fail("Preflight must never call a model")

    monkeypatch.setattr(PrinterAnalyzer, "analyze", no_analysis)


def _path(context):
    camera_id = context.manager.list()[0].descriptor.id
    return f"/api/cameras/{camera_id}/timelapse/preflight"


def _storage(context, handler):
    peer = Peer("storage", "storage.tailnet", "http://storage:8088", online=True)
    context.config.storage.node = peer.key
    context.cluster._peers = [peer]
    context.cluster._by_key = {peer.key: peer}
    context.cluster._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _remote_capabilities(enabled=True):
    return {
        "host": "storage.tailnet",
        "printer_analyzer": {
            "enabled": enabled,
            "endpoint": "http://user:private-password@ai.tailnet:11434/?token=private-token#secret",
            "model": "printer-model",
            "reachability": "unchecked",
        },
        "postprocess": {
            "available": True,
            "default_engine": "ffmpeg",
            "default_target_fps": 60,
            "engines": [{"id": "ffmpeg", "label": "FFmpeg", "available": True,
                         "source": "system"}],
        },
    }


def test_local_configuration_is_not_a_model_health_check(client, context):
    context.printer_analyzer.config = AIConfig(
        enabled=True,
        base_url="http://user:private-password@ai.tailnet:11434/ollama?token=secret#secret",
        model="printer-model",
    )
    response = client.get(_path(context))
    assert response.status_code == 200
    body = response.json()
    assert body["route_status"] == "local"
    assert body["camera_host"] == body["capture_host"] == context.local_host
    assert body["capabilities"]["printer_analyzer"] == {
        "enabled": True,
        "endpoint": "http://ai.tailnet:11434/ollama",
        "model": "printer-model",
        "reachability": "unchecked",
    }
    assert body["capabilities"]["postprocess"]["available"] is False
    assert context.timelapse.list() == []
    assert "private-password" not in response.text
    assert "secret" not in response.text


@pytest.mark.parametrize("remote_enabled", [True, False])
def test_preflight_uses_storage_capabilities_instead_of_source(client, context, remote_enabled):
    context.printer_analyzer.config = AIConfig(enabled=not remote_enabled)
    calls = []

    def respond(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json=_remote_capabilities(remote_enabled))

    _storage(context, respond)
    response = client.get(_path(context))
    assert response.status_code == 200
    body = response.json()
    assert body["camera_host"] == context.local_host
    assert body["capture_host"] == "storage.tailnet"
    assert body["route_status"] == "reachable"
    assert body["capabilities"]["printer_analyzer"]["enabled"] is remote_enabled
    assert body["capabilities"]["printer_analyzer"]["endpoint"] == "http://ai.tailnet:11434/"
    assert body["capabilities"]["postprocess"]["available"] is True
    assert calls == [("GET", "http://storage:8088/api/timelapse-capabilities")]
    assert "private-" not in response.text
    assert context.timelapse.list() == []


def test_local_capabilities_never_follow_storage_route(client, context):
    def no_requests(request):
        pytest.fail("Local-only capabilities must never follow another storage route")

    _storage(context, no_requests)
    response = client.get("/api/timelapse-capabilities")
    assert response.status_code == 200
    assert response.json()["host"] == context.local_host
    assert response.json()["printer_analyzer"]["enabled"] is False


def test_unresolved_storage_reports_unknown_with_current_local_fallback(client, context):
    context.config.storage.node = "unknown-storage"
    response = client.get(_path(context))
    assert response.status_code == 200
    body = response.json()
    assert body["route_status"] == "unknown"
    assert body["capture_host"] == context.local_host
    assert body["capabilities"]["host"] == context.local_host
    assert "falls back" in body["message"]


@pytest.mark.parametrize("exception,status", [
    (httpx.ConnectError, "unreachable"),
    (httpx.ConnectTimeout, "unreachable"),
    (httpx.ReadTimeout, "unknown"),
    (httpx.RemoteProtocolError, "unknown"),
])
def test_failed_check_does_not_claim_health_or_modify_routing(client, context, exception, status):
    def fail(request):
        raise exception("private-password must not appear in API output", request=request)

    _storage(context, fail)
    response = client.get(_path(context))
    assert response.status_code == 200
    body = response.json()
    assert body["route_status"] == status
    assert body["capture_host"] == "storage.tailnet"
    assert body["capabilities"] is None
    assert context.capture.target() == ("storage", "http://storage:8088")
    assert context.capture.last_error == ""
    assert "private-password" not in response.text
    assert context.timelapse.list() == []


@pytest.mark.parametrize("status", [301, 404, 409, 500])
def test_non_successful_or_old_peer_capabilities_remain_unknown(client, context, status):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={"location": "http://elsewhere/private"},
                              json={"detail": "private error"})

    _storage(context, respond)
    response = client.get(_path(context))
    body = response.json()
    assert body["route_status"] == "unknown"
    assert body["capabilities"] is None
    assert f"HTTP {status}" in body["message"]
    assert "private" not in response.text
    assert calls == ["http://storage:8088/api/timelapse-capabilities"]


@pytest.mark.parametrize("payload", [[], {"host": "storage"}, {"available": True}])
def test_malformed_capabilities_remain_unknown(client, context, payload):
    _storage(context, lambda request: httpx.Response(200, json=payload))
    response = client.get(_path(context))
    assert response.json()["route_status"] == "unknown"
    assert response.json()["capabilities"] is None


def test_unknown_camera_has_no_capability_side_effects(client, context):
    _storage(context, lambda request: pytest.fail("Unknown camera must not contact a peer"))
    response = client.get("/api/cameras/not/a/camera/timelapse/preflight")
    assert response.status_code == 404


def test_rife_without_ffmpeg_cannot_promise_smoothing(client, monkeypatch):
    from tailcam.timelapse import rife

    monkeypatch.setattr(rife, "rife_available", lambda configured: True)
    body = client.get("/api/timelapse-capabilities").json()
    assert body["postprocess"]["engines"][1]["available"] is True
    assert body["postprocess"]["available"] is False


@pytest.mark.parametrize("endpoint,expected", [
    ("https://user:secret@[::1]:11434/base?key=secret#secret", "https://[::1]:11434/base"),
    ("http://user:secret@host:bad?key=secret", "Invalid endpoint"),
    ("http://user:secret@[bad", "Invalid endpoint"),
    ("secret", "Invalid endpoint"),
])
def test_display_endpoint_never_echoes_credentials(endpoint, expected):
    assert display_endpoint(endpoint) == expected
