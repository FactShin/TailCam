from __future__ import annotations

import time

from tailcam.camera.worker import CameraStatus
from tailcam.management.capabilities import NodeCapabilityService
from tailcam.management.health import NodeHealthService
from tailcam.tailscale.client import TailscaleStatus


def _healthy_tailscale() -> TailscaleStatus:
    return TailscaleStatus(
        installed=True,
        running=True,
        ipv4="100.64.0.10",
        magic_dns="tailcam.example.ts.net",
        app_capabilities_supported=True,
    )


def _health_service(
    context,
    monkeypatch,
    *,
    update_available: bool = False,
    ai_health: tuple[bool, str | None] | None = None,
) -> NodeHealthService:
    monkeypatch.setattr(context.tailscale, "status", _healthy_tailscale)
    monkeypatch.setattr(
        context.tailscale,
        "access_url",
        lambda local_port, served, https_port=8443: "https://tailcam.example.ts.net:8443/",
    )
    health = ai_health if ai_health is not None else (True, context.config.ai.model)
    monkeypatch.setattr(context.analyzer, "health", lambda: health)
    latest = "0.91.0" if update_available else "0.90.0"
    return NodeHealthService(
        context,
        update_checker=lambda use_cache=True: ("0.90.0", latest, update_available),
        started_monotonic=time.monotonic() - 10.0,
    )


def test_capability_identifiers_are_stable() -> None:
    capabilities = NodeCapabilityService().snapshot()

    assert capabilities.api_version == "1"
    assert capabilities.actions == frozenset({"reload"})
    assert capabilities.capabilities == frozenset(
        {
            "camera.view",
            "camera.control",
            "camera.record",
            "node.health",
            "node.reload",
            "node.audit",
            "ai.ollama.status",
        }
    )


def test_healthy_synthetic_node(context, monkeypatch) -> None:
    context.manager.start_all()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if any(
            context.manager.status(cam.descriptor.id) == CameraStatus.ONLINE
            for cam in context.manager.list()
        ):
            break
        time.sleep(0.05)

    health = _health_service(context, monkeypatch).snapshot()

    assert health.version == "0.90.0"
    assert health.host == context.local_host
    assert health.uptime_seconds >= 10.0
    assert health.tailscale_running is True
    assert health.camera_total >= 1
    assert health.camera_online >= 1
    assert health.camera_recording == 0
    assert health.media_bytes == 0
    assert health.ai_reachable is True
    assert health.update_available is False
    assert not [issue for issue in health.issues if issue.severity == "error"]


def test_offline_camera_creates_warning_issue(context, monkeypatch) -> None:
    health = _health_service(context, monkeypatch).snapshot()

    assert health.camera_total >= 1
    assert health.camera_offline >= 1
    assert any(issue.code == "camera.offline" for issue in health.issues)


def test_stopped_tailscale_creates_issue(context, monkeypatch) -> None:
    monkeypatch.setattr(
        context.tailscale,
        "status",
        lambda: TailscaleStatus(installed=True, running=False, ipv4=None, magic_dns=None),
    )
    monkeypatch.setattr(
        context.tailscale,
        "access_url",
        lambda local_port, served, https_port=8443: f"http://localhost:{local_port}/",
    )

    health = NodeHealthService(
        context,
        update_checker=lambda use_cache=True: ("0.90.0", "0.90.0", False),
    ).snapshot()

    assert health.tailscale_installed is True
    assert health.tailscale_running is False
    assert any(issue.code == "tailscale.stopped" for issue in health.issues)


def test_missing_tailscale_app_caps_creates_warning_issue(context, monkeypatch) -> None:
    monkeypatch.setattr(
        context.tailscale,
        "status",
        lambda: TailscaleStatus(
            installed=True,
            running=True,
            ipv4="100.64.0.10",
            magic_dns="tailcam.example.ts.net",
            app_capabilities_supported=False,
        ),
    )
    monkeypatch.setattr(
        context.tailscale,
        "access_url",
        lambda local_port, served, https_port=8443: "https://tailcam.example.ts.net:8443/",
    )
    monkeypatch.setattr(context.analyzer, "health", lambda: (True, context.config.ai.model))

    health = NodeHealthService(
        context,
        update_checker=lambda use_cache=True: ("0.90.0", "0.90.0", False),
    ).snapshot()

    assert any(issue.code == "tailscale.app_caps_unavailable" for issue in health.issues)


def test_unavailable_ai_creates_issue(context, monkeypatch) -> None:
    context.config.ai.enabled = True

    health = _health_service(context, monkeypatch, ai_health=(False, None)).snapshot()

    assert health.ai_enabled is True
    assert health.ai_reachable is False
    assert health.ai_model_present is False
    assert any(issue.code == "ai.unreachable" for issue in health.issues)


def test_update_available_creates_issue(context, monkeypatch) -> None:
    health = _health_service(context, monkeypatch, update_available=True).snapshot()

    assert health.update_current == "0.90.0"
    assert health.update_latest == "0.91.0"
    assert health.update_available is True
    assert any(issue.code == "update.available" for issue in health.issues)
