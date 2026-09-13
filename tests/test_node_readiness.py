from __future__ import annotations

import builtins
import json
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tailcam.camera.worker import CameraStatus
from tailcam.config import AppConfig
from tailcam.management import readiness
from tailcam.management.health import NodeHealthService
from tailcam.management.readiness import NodeReadinessService
from tailcam.timelapse import ffmpeg
from tailcam.web import routes_fleet_v1, routes_node_v1

_REAL_FFMPEG_PRESENT = ffmpeg.passive_ffmpeg_present
_REAL_INSTALLED = readiness._installed
_AVAILABLE = (
    "ready", "ollama.model_available",
    "The selected model is installed; inference and vision support were not tested.",
)


def _forbidden(*args, **kwargs):
    raise AssertionError("A readiness snapshot must not start workloads or contact services")


@pytest.fixture
def diagnostic_context(tmp_path, monkeypatch):
    config = AppConfig()
    config.storage.media_dir = str(tmp_path)
    config.ai.enabled = True
    config.ai.base_url = "http://127.0.0.1:1"
    config.detection.enabled = True
    ctx = SimpleNamespace(
        config=config,
        active_roles=frozenset({"capture", "storage", "analysis", "training"}),
        node_id="11111111-2222-4333-8444-555555555555",
        manager=SimpleNamespace(
            list=Mock(return_value=[]), status=Mock(return_value=CameraStatus.OFFLINE),
            discover=_forbidden, start_all=_forbidden, restart=_forbidden,
        ),
        detector=SimpleNamespace(
            status=Mock(return_value=SimpleNamespace(status="idle")),
            ensure_ready=_forbidden, detect=_forbidden,
        ),
        analyzer=SimpleNamespace(health=_forbidden),
    )
    ctx.has_role = lambda role: role in ctx.active_roles
    monkeypatch.setattr(
        readiness.hostinfo, "profile",
        lambda: SimpleNamespace(cpu_count=4, total_ram_bytes=8 * 1024**3),
    )
    monkeypatch.setattr(readiness, "_ffmpeg_present", lambda: False)
    monkeypatch.setattr(readiness, "_installed", lambda name: False)
    monkeypatch.setattr(readiness, "probe_ollama", _forbidden)
    monkeypatch.setattr(
        readiness.shutil, "disk_usage", lambda path: SimpleNamespace(total=4096, free=2048),
    )
    monkeypatch.setattr(readiness.os, "access", lambda path, mode: True)
    ctx.readiness = NodeReadinessService(ctx)
    return ctx


@pytest.fixture
def diagnostic_clock(monkeypatch):
    clock = SimpleNamespace(wall=1000.0, monotonic=100.0)
    monkeypatch.setattr(
        readiness, "time",
        SimpleNamespace(time=lambda: clock.wall, monotonic=lambda: clock.monotonic),
    )
    return clock


def _tasks(snapshot):
    return {task.id: task for task in snapshot.tasks}


def _ollama(ctx, *, probe=False):
    return _tasks(ctx.readiness.snapshot(probe=probe))["analysis.ollama"]


def test_passive_snapshot_never_imports_models_resolves_or_runs_workloads(
    diagnostic_context, monkeypatch,
):
    ctx = diagnostic_context
    real_import = builtins.__import__
    discovered = []

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"torch", "ultralytics", "imageio_ffmpeg"}:
            raise AssertionError(f"Readiness imported runtime package: {name}")
        return real_import(name, *args, **kwargs)

    def find_spec(name):
        assert "." not in name, "Dotted spec lookup may import a parent package"
        discovered.append(name)
        return SimpleNamespace(origin=None)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(readiness.importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(readiness, "_ffmpeg_present", _REAL_FFMPEG_PRESENT)
    monkeypatch.setattr(readiness, "_installed", _REAL_INSTALLED)
    monkeypatch.setattr(ffmpeg, "_KNOWN_BINARIES", {})
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", _forbidden)
    monkeypatch.setattr(readiness.os, "chmod", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(httpx.Client, "request", _forbidden)
    monkeypatch.setattr(httpx.Client, "stream", _forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", _forbidden)

    snapshot = ctx.readiness.snapshot()

    assert set(discovered) == {"imageio_ffmpeg", "torch", "ultralytics"}
    assert snapshot.capacity.cpu_count == 4
    assert snapshot.capacity.total_ram_bytes == 8 * 1024**3
    assert _tasks(snapshot)["analysis.ollama"].state == "unchecked"
    assert _tasks(snapshot)["training"].state == "unchecked"
    ctx.detector.status.assert_called_once_with()


@pytest.mark.parametrize("probe", [False, True])
def test_active_roles_gate_every_observation_before_probing(diagnostic_context, monkeypatch, probe):
    ctx = diagnostic_context
    ctx.active_roles = frozenset()
    # Saved roles still enable all tasks, but the running process is a hub.
    assert "analysis" in ctx.config.node.roles
    ctx.manager.list = _forbidden
    ctx.detector.status = _forbidden
    monkeypatch.setattr(readiness.shutil, "disk_usage", _forbidden)
    monkeypatch.setattr(readiness.os, "access", _forbidden)
    monkeypatch.setattr(readiness, "_ffmpeg_present", _forbidden)
    monkeypatch.setattr(readiness, "_installed", _forbidden)

    snapshot = ctx.readiness.snapshot(probe=probe)

    assert len(snapshot.tasks) == 6
    assert all(task.state == "disabled" and task.code == "role_disabled" for task in snapshot.tasks)
    assert snapshot.capacity.media_free_bytes is None
    assert snapshot.capacity.media_total_bytes is None
    assert snapshot.capacity.media_writable is None


def test_saved_role_change_does_not_claim_running_workloads_stopped(diagnostic_context):
    ctx = diagnostic_context
    ctx.config.node.roles = []
    assert _ollama(ctx).state == "unchecked"
    assert _tasks(ctx.readiness.snapshot())["storage.local"].state == "ready"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [([], "unavailable"), ([CameraStatus.DEGRADED], "unavailable"),
     ([CameraStatus.ONLINE, CameraStatus.OFFLINE], "ready")],
)
def test_capture_uses_cached_camera_status(diagnostic_context, statuses, expected):
    ctx = diagnostic_context
    cameras = [SimpleNamespace(descriptor=SimpleNamespace(id=str(i))) for i in range(len(statuses))]
    ctx.manager.list.return_value = cameras
    ctx.manager.status.side_effect = lambda camera_id: statuses[int(camera_id)]

    task = _tasks(ctx.readiness.snapshot())["capture"]

    assert task.state == expected
    if expected == "ready":
        assert "1 camera(s)" in task.detail


@pytest.mark.parametrize(
    ("free", "writable", "state", "code"),
    [(2048, True, "ready", "media_accessible"),
     (0, True, "unavailable", "media_full"),
     (2048, False, "unavailable", "media_not_writable")],
)
def test_storage_capacity_preserves_zero_and_access_status(
    diagnostic_context, monkeypatch, free, writable, state, code,
):
    monkeypatch.setattr(
        readiness.shutil, "disk_usage", lambda path: SimpleNamespace(total=4096, free=free),
    )
    monkeypatch.setattr(readiness.os, "access", lambda path, mode: writable)

    snapshot = diagnostic_context.readiness.snapshot()

    assert (_tasks(snapshot)["storage.local"].state, _tasks(snapshot)["storage.local"].code) == (
        state, code,
    )
    assert snapshot.capacity.media_free_bytes == free
    assert snapshot.capacity.media_total_bytes == 4096
    assert snapshot.capacity.media_writable is writable


def test_missing_storage_never_creates_directory(diagnostic_context, monkeypatch, tmp_path):
    missing = tmp_path / "unmounted" / "media"
    diagnostic_context.config.storage.media_dir = str(missing)
    monkeypatch.setattr(readiness.shutil, "disk_usage", _forbidden)

    snapshot = diagnostic_context.readiness.snapshot()

    assert _tasks(snapshot)["storage.local"].code == "media_directory_missing"
    assert snapshot.capacity.media_writable is None
    assert snapshot.capacity.media_free_bytes is None
    assert not missing.parent.exists()


def test_unreadable_storage_error_is_sanitized(diagnostic_context, monkeypatch):
    monkeypatch.setattr(
        readiness.shutil, "disk_usage",
        Mock(side_effect=OSError("private mount path /secret/access-token")),
    )

    task = _tasks(diagnostic_context.readiness.snapshot())["storage.local"]

    assert task.code == "media_unreadable"
    assert "secret" not in task.detail
    assert "access-token" not in task.detail


@pytest.mark.parametrize(
    ("status", "state", "code"),
    [("ready", "ready", "detector_loaded"), ("error", "unavailable", "detector_error"),
     ("loading", "unchecked", "model_not_loaded"), ("idle", "unchecked", "model_not_loaded")],
)
def test_detector_only_reports_cached_state_without_error_details(
    diagnostic_context, status, state, code,
):
    diagnostic_context.detector.status.return_value = SimpleNamespace(
        status=status, error="secret raw runtime error", detail="http://user:password@private/",
    )
    task = _tasks(diagnostic_context.readiness.snapshot())["detection.builtin"]
    assert (task.state, task.code) == (state, code)
    assert "secret" not in task.detail and "password" not in task.detail


@pytest.mark.parametrize(("enabled", "node", "code"), [
    (False, "", "feature_disabled"), (True, "remote-node", "remote_execution"),
])
def test_disabled_or_remote_detector_does_not_inspect_local_runtime(
    diagnostic_context, enabled, node, code,
):
    diagnostic_context.config.detection.enabled = enabled
    diagnostic_context.config.detection.node = node
    diagnostic_context.detector.status = _forbidden
    assert _tasks(diagnostic_context.readiness.snapshot())["detection.builtin"].code == code


@pytest.mark.parametrize("present", [False, True])
def test_installed_encoder_and_training_packages_are_never_claimed_ready(
    diagnostic_context, monkeypatch, present,
):
    monkeypatch.setattr(readiness, "_ffmpeg_present", lambda: present)
    monkeypatch.setattr(readiness, "_installed", lambda name: present)
    tasks = _tasks(diagnostic_context.readiness.snapshot())
    assert tasks["processing.ffmpeg"].state == ("unchecked" if present else "unavailable")
    assert tasks["training"].state == ("unchecked" if present else "unavailable")
    if present:
        assert "GPU support and available memory untested" in tasks["training"].detail


def test_encoder_checks_known_paths_for_current_platform(monkeypatch, tmp_path):
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"binary placeholder")
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg, "_KNOWN_BINARIES", {ffmpeg.sys.platform: [str(binary)]})
    monkeypatch.setattr(ffmpeg.os, "access", lambda path, mode: path == binary)
    monkeypatch.setattr(ffmpeg.importlib.util, "find_spec", _forbidden)
    assert _REAL_FFMPEG_PRESENT() is True


def test_bundled_encoder_without_execute_access_is_not_repaired(monkeypatch, tmp_path):
    package = tmp_path / "imageio_ffmpeg"
    binaries = package / "binaries"
    binaries.mkdir(parents=True)
    (binaries / "ffmpeg-platform").write_bytes(b"binary placeholder")
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg, "_KNOWN_BINARIES", {})
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(
        ffmpeg.importlib.util, "find_spec",
        lambda name: SimpleNamespace(origin=str(package / "__init__.py")),
    )
    monkeypatch.setattr(ffmpeg.os, "access", lambda path, mode: False)
    monkeypatch.setattr(ffmpeg.os, "chmod", _forbidden)
    assert _REAL_FFMPEG_PRESENT() is False


@pytest.mark.parametrize(("enabled", "provider", "model", "state", "code"), [
    (False, "ollama", "moondream", "disabled", "feature_disabled"),
    (True, "custom-provider", "moondream", "unchecked", "probe_unsupported"),
    (True, "ollama", "  ", "unavailable", "model_not_configured"),
])
def test_ollama_feature_and_configuration_gate_explicit_probes(
    diagnostic_context, enabled, provider, model, state, code,
):
    ai = diagnostic_context.config.ai
    ai.enabled, ai.provider, ai.model = enabled, provider, model
    task = _ollama(diagnostic_context, probe=True)
    assert (task.state, task.code) == (state, code)


def test_explicit_probe_cache_expires_without_passive_network_refresh(
    diagnostic_context, diagnostic_clock, monkeypatch,
):
    probe = Mock(return_value=_AVAILABLE)
    monkeypatch.setattr(readiness, "probe_ollama", probe)
    ctx, clock = diagnostic_context, diagnostic_clock
    assert _ollama(ctx).code == "runtime_unchecked"
    first = _ollama(ctx, probe=True)
    probe.assert_called_once_with(ctx.config.ai.base_url, ctx.config.ai.model)
    assert first.state == "ready"
    assert first.checked_at == 1000.0
    clock.wall += 10
    clock.monotonic += 10
    snapshot = ctx.readiness.snapshot()
    assert snapshot.checked_at == 1010.0
    assert _tasks(snapshot)["analysis.ollama"] == first
    clock.monotonic += ctx.readiness.CACHE_SECONDS
    assert _ollama(ctx).code == "runtime_unchecked"
    assert probe.call_count == 1
    clock.wall += 30
    refreshed = _ollama(ctx, probe=True)
    assert refreshed.checked_at == 1040.0
    assert probe.call_count == 2


def test_probe_throttling_reuses_both_success_and_failure_snapshots(
    diagnostic_context, diagnostic_clock, monkeypatch,
):
    probe = Mock(side_effect=[_AVAILABLE, (
        "unavailable", "ollama.timeout", "The Ollama inventory probe timed out.",
    )])
    monkeypatch.setattr(readiness, "probe_ollama", probe)
    first = _ollama(diagnostic_context, probe=True)
    assert _ollama(diagnostic_context, probe=True) == first
    assert probe.call_count == 1
    diagnostic_clock.monotonic += NodeReadinessService.PROBE_INTERVAL
    failed = _ollama(diagnostic_context, probe=True)
    assert failed.state == "unavailable"
    assert _ollama(diagnostic_context, probe=True) == failed
    assert _ollama(diagnostic_context) == failed
    assert probe.call_count == 2


@pytest.mark.parametrize(("field", "value"), [
    ("base_url", "https://new-runtime.example.invalid"), ("model", "new-model"),
    ("provider", "custom-provider"), ("enabled", False),
])
def test_configuration_change_invalidates_cached_runtime_result(
    diagnostic_context, diagnostic_clock, monkeypatch, field, value,
):
    probe = Mock(return_value=_AVAILABLE)
    monkeypatch.setattr(readiness, "probe_ollama", probe)
    assert _ollama(diagnostic_context, probe=True).state == "ready"
    setattr(diagnostic_context.config.ai, field, value)
    assert _ollama(diagnostic_context).state != "ready"
    # A changed endpoint/model also cannot evade the global probe interval.
    assert _ollama(diagnostic_context, probe=True).state != "ready"
    assert probe.call_count == 1


def test_changed_endpoint_is_checked_after_throttle_interval(
    diagnostic_context, diagnostic_clock, monkeypatch,
):
    probe = Mock(return_value=_AVAILABLE)
    monkeypatch.setattr(readiness, "probe_ollama", probe)
    _ollama(diagnostic_context, probe=True)
    diagnostic_context.config.ai.base_url = "https://new-runtime.example.invalid"
    assert _ollama(diagnostic_context, probe=True).state == "unchecked"
    diagnostic_clock.monotonic += NodeReadinessService.PROBE_INTERVAL
    assert _ollama(diagnostic_context, probe=True).state == "ready"
    assert probe.call_count == 2
    probe.assert_called_with(
        "https://new-runtime.example.invalid", diagnostic_context.config.ai.model,
    )


@pytest.mark.parametrize("change", ["model", "enabled", "roles"])
def test_settings_changed_during_probe_cannot_publish_obsolete_readiness(
    diagnostic_context, monkeypatch, change,
):
    ctx = diagnostic_context

    def probe(base_url, model):
        if change == "model":
            ctx.config.ai.model = "different-model"
        elif change == "enabled":
            ctx.config.ai.enabled = False
        else:
            ctx.active_roles = frozenset()
        return _AVAILABLE

    monkeypatch.setattr(readiness, "probe_ollama", probe)
    assert _ollama(ctx, probe=True).code == "configuration_changed"
    assert _ollama(ctx).state != "ready"


def test_concurrent_probes_are_coalesced_and_passive_reads_do_not_wait(
    diagnostic_context, diagnostic_clock, monkeypatch,
):
    started, release = threading.Event(), threading.Event()
    calls = []

    def probe(base_url, model):
        calls.append((base_url, model))
        started.set()
        assert release.wait(3), "Test did not release the inventory probe"
        return _AVAILABLE

    monkeypatch.setattr(readiness, "probe_ollama", probe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(_ollama, diagnostic_context, probe=True)
        try:
            assert started.wait(3)
            assert _ollama(diagnostic_context).state == "unchecked"
            assert _ollama(diagnostic_context, probe=True).state == "unchecked"
            diagnostic_clock.monotonic += NodeReadinessService.PROBE_INTERVAL
            assert _ollama(diagnostic_context, probe=True).code == "probe_in_progress"
            assert len(calls) == 1
        finally:
            release.set()
        assert pending.result(timeout=3).state == "ready"
    assert _ollama(diagnostic_context).state == "ready"


def _client(ctx):
    # No create_app/lifespan: these route tests cannot start cameras or services.
    app = FastAPI()
    app.state.ctx = ctx
    app.include_router(routes_node_v1.router)
    app.include_router(routes_fleet_v1.router)
    return TestClient(app, base_url="http://localhost:8088", client=("127.0.0.1", 50000))


@pytest.mark.parametrize("path", [
    "/api/v1/node/capabilities", "/api/v1/fleet/nodes/local/capabilities",
])
def test_local_capability_routes_only_probe_on_explicit_query(
    diagnostic_context, monkeypatch, path,
):
    probe = Mock(return_value=_AVAILABLE)
    monkeypatch.setattr(readiness, "probe_ollama", probe)
    with _client(diagnostic_context) as client:
        passive = client.get(path)
        assert passive.status_code == 200
        assert probe.call_count == 0
        result = client.get(path + "?probe=true")
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["readiness"]["probe_supported"] is True
    assert payload["node_id"] == diagnostic_context.node_id
    assert payload["capabilities"] == passive.json()["capabilities"]
    task = next(t for t in payload["readiness"]["tasks"] if t["id"] == "analysis.ollama")
    assert task["state"] == "ready"
    assert probe.call_count == 1


def _remote_payload():
    return {
        "api_version": "1", "capabilities": ["node.health"], "actions": ["reload"],
        "principal": {
            "actor": "local", "display_name": None, "source": "local", "verified": True,
            "roles": ["admin"],
        },
    }


def _remote_client(ctx, response):
    request = AsyncMock(return_value=response)
    ctx.cluster = SimpleNamespace(
        peer_base=lambda key: "https://peer.example.invalid:8443" if key == "peer" else None,
        client=lambda: SimpleNamespace(request=request), peers=AsyncMock(return_value=[]),
    )
    return request


@pytest.mark.parametrize("legacy", [False, True])
def test_fleet_relay_preserves_explicit_probe_and_optional_legacy_readiness(
    diagnostic_context, legacy,
):
    payload = _remote_payload()
    if not legacy:
        payload["readiness"] = asdict(diagnostic_context.readiness.snapshot())
    request = _remote_client(diagnostic_context, httpx.Response(200, json=payload))
    with _client(diagnostic_context) as client:
        passive = client.get("/api/v1/fleet/nodes/peer/capabilities")
        explicit = client.get("/api/v1/fleet/nodes/peer/capabilities?probe=true")
    assert passive.status_code == explicit.status_code == 200
    assert request.call_args_list[0].kwargs["params"] is None
    assert request.call_args_list[1].kwargs["params"] == {"probe": "true"}
    assert request.call_args.args == ("GET", "https://peer.example.invalid:8443/api/v1/node/capabilities")
    if legacy:
        assert explicit.json()["readiness"] is None
    else:
        assert explicit.json()["readiness"]["tasks"] == list(payload["readiness"]["tasks"])


def test_fleet_transport_error_does_not_expose_peer_url_or_credentials(diagnostic_context):
    request = _remote_client(diagnostic_context, None)
    request.side_effect = httpx.ConnectError("https://user:secret@peer.private:8443 access-token")
    with _client(diagnostic_context) as client:
        result = client.get("/api/v1/fleet/nodes/peer/capabilities?probe=true")
    assert result.status_code == 502
    for private in ["secret", "peer.private", "access-token", "https://"]:
        assert private not in result.text


@pytest.mark.parametrize("status_code", [401, 404, 503])
def test_fleet_peer_error_body_is_sanitized_while_preserving_status(
    diagnostic_context, status_code,
):
    _remote_client(diagnostic_context, httpx.Response(status_code, json={
        "detail": "https://user:secret@peer.private:8443 access-token",
    }))
    with _client(diagnostic_context) as client:
        result = client.get("/api/v1/fleet/nodes/peer/capabilities?probe=true")
    assert result.status_code == status_code
    for private in ["secret", "peer.private", "access-token", "https://"]:
        assert private not in result.text


def _replace_field(payload, field, value):
    target = payload
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = value


@pytest.mark.parametrize("field", [
    ("readiness",), ("readiness", "capacity"), ("readiness", "tasks"),
    ("readiness", "tasks", 0), ("readiness", "tasks", 0, "state"),
])
def test_malformed_fleet_readiness_returns_safe_502_without_validation_details(
    diagnostic_context, field, caplog,
):
    payload = _remote_payload()
    payload["readiness"] = asdict(diagnostic_context.readiness.snapshot())
    # Convert the dataclass tuple so every nested field can be replaced.
    payload["readiness"]["tasks"] = list(payload["readiness"]["tasks"])
    private = "https://user:secret@peer.private/ollama?token=access-token"
    _replace_field(payload, field, private)
    _remote_client(diagnostic_context, httpx.Response(200, json=payload))

    with _client(diagnostic_context) as client:
        result = client.get("/api/v1/fleet/nodes/peer/capabilities?probe=true")

    assert result.status_code == 502
    assert result.json() == {"detail": "peer returned invalid capabilities"}
    for secret in [private, "access-token", "peer.private"]:
        assert secret not in result.text
        assert secret not in caplog.text


@pytest.mark.parametrize("field", [
    ("checked_at",), ("tasks", 0, "checked_at"),
    ("capacity", "cpu_count"), ("capacity", "total_ram_bytes"),
    ("capacity", "media_free_bytes"), ("capacity", "media_total_bytes"),
])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), float("-inf")])
def test_fleet_readiness_rejects_negative_and_nonfinite_numbers(
    diagnostic_context, field, value,
):
    payload = _remote_payload()
    payload["readiness"] = asdict(diagnostic_context.readiness.snapshot())
    _replace_field(payload["readiness"], field, value)
    # A malformed peer can emit NaN/Infinity. httpx's json= intentionally
    # rejects those itself, so supply the exact upstream body instead.
    _remote_client(diagnostic_context, httpx.Response(200, content=json.dumps(payload)))

    with _client(diagnostic_context) as client:
        result = client.get("/api/v1/fleet/nodes/peer/capabilities")

    assert result.status_code == 502
    assert result.json() == {"detail": "peer returned invalid capabilities"}


@pytest.mark.parametrize("media_bytes", [None, 0])
def test_fleet_readiness_preserves_valid_zero_and_unknown_measurements(
    diagnostic_context, media_bytes,
):
    payload = _remote_payload()
    payload["readiness"] = asdict(diagnostic_context.readiness.snapshot())
    reported = payload["readiness"]
    reported["checked_at"] = 0
    for task in reported["tasks"]:
        task["checked_at"] = 0
    reported["capacity"] = {
        "cpu_count": 0, "total_ram_bytes": 0,
        "media_free_bytes": media_bytes, "media_total_bytes": media_bytes,
        "media_writable": None,
    }
    _remote_client(diagnostic_context, httpx.Response(200, json=payload))

    with _client(diagnostic_context) as client:
        result = client.get("/api/v1/fleet/nodes/peer/capabilities")

    assert result.status_code == 200
    returned = result.json()["readiness"]
    assert returned["capacity"] == reported["capacity"]
    assert returned["checked_at"] == 0
    assert all(task["checked_at"] == 0 for task in returned["tasks"])


def test_health_issue_does_not_expose_configured_analyzer_url(diagnostic_context):
    ctx = diagnostic_context
    ctx.config.ai.base_url = "https://user:secret@private.example.invalid/ollama?token=private-key"
    ctx.local_host, ctx.served = "test-node", False
    ctx.tailscale = SimpleNamespace(
        status=lambda: SimpleNamespace(installed=False, running=False),
        access_url=lambda *args: "http://localhost:8088/",
    )
    ctx.analyzer.health = lambda: (False, None)
    ctx.capture = SimpleNamespace(is_recording=lambda camera_id: False)
    ctx.gallery = SimpleNamespace(total_bytes=lambda: 0)
    ctx.store = SimpleNamespace(total_timelapse_bytes=lambda: 0)

    snapshot = NodeHealthService(
        ctx, update_checker=lambda use_cache: ("1.9.2", "1.9.2", False),
    ).snapshot()

    issue = next(issue for issue in snapshot.issues if issue.code == "ai.unreachable")
    assert "Models page" in issue.detail
    for private in ["secret", "private.example.invalid", "private-key", ctx.config.ai.base_url]:
        assert private not in repr(asdict(snapshot))
