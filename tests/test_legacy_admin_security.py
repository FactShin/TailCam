"""Restricted agents cannot bypass training approval through legacy settings.

These apps have no lifespan or AppContext: every effect boundary is an isolated
stub, so the exploit regressions never install code, load models or touch media.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tailcam.mcp.client import TailcamClient
from tailcam.mcp.errors import TailcamMcpError
from tailcam.security.principal import RequestPrincipal, TailCamRole
from tailcam.web import routes_active, routes_api, routes_proxy
from tailcam.web.deps import get_context
from tailcam.web.legacy_admin import legacy_admin_request, require_storage_admin
from tailcam.web.routes_node_v1 import require_admin
from tailcam.web.schemas import AIInfo, PluginsMarketInfo, StorageInfo
from tailcam.web.security import SecurityMiddleware

ADMIN_ENDPOINTS = [
    ("POST", "/api/ai", {"model": "unapproved", "enabled": True}),
    ("POST", "/api/ai/pull", {"model": "unapproved"}),
    ("POST", "/api/ai/load", {"model": "unapproved"}),
    ("POST", "/api/plugins/market/install", {"id": "curated-plugin"}),
    ("DELETE", "/api/plugins/installed/plugin", None),
    ("POST", "/api/plugins/installed/plugin/toggle", {"enabled": True}),
    ("POST", "/api/plugins/reload", None),
    ("POST", "/api/mcp", {"enabled": True, "http_enabled": True}),
    ("POST", "/api/notifications", {"webhook_url": "http://127.0.0.1:9"}),
    ("POST", "/api/integrations/homekit", {"enabled": True}),
    ("POST", "/api/integrations/homekit/reset", None),
    ("POST", "/api/integrations/homeassistant", {"mqtt_host": "unapproved.invalid"}),
    ("GET", "/api/notifications", None),
    ("GET", "/api/integrations", None),
]


class NoEffects:
    def __getattr__(self, name):
        raise AssertionError(f"unauthorized handler accessed context.{name}")


def _app(ctx=None):
    app = FastAPI()
    app.add_middleware(SecurityMiddleware)
    app.include_router(routes_api.router)
    app.include_router(routes_active.router)
    app.include_router(routes_proxy.router)
    app.dependency_overrides[get_context] = lambda: ctx if ctx is not None else NoEffects()
    return app


def _headers(role):
    return {
        "tailscale-user-login": "restricted@example.test",
        "tailscale-app-capabilities": json.dumps({
            "factshin.github.io/cap/tailcam": [{"roles": [role] if role else []}],
        }),
    }


@pytest.mark.parametrize("role", ["viewer", "operator", ""])
@pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
def test_restricted_rest_cannot_reconfigure_or_read_credentials(role, method, path, body):
    with TestClient(
        _app(), base_url="http://localhost", client=("127.0.0.1", 51000),
        headers=_headers(role),
    ) as client:
        response = client.request(method, path, json=body)
    assert response.status_code == 403
    assert response.json() == {"detail": "admin role required"}


def test_untrusted_network_cannot_forge_admin_headers():
    with TestClient(
        _app(), base_url="http://localhost", client=("192.0.2.1", 51000),
        headers=_headers("admin"),
    ) as client:
        response = client.post("/api/plugins/market/install", json={"id": "plugin"})
    assert response.status_code == 403


@pytest.mark.anyio
async def test_internal_mcp_principal_cannot_use_legacy_rest_as_local_admin():
    principal = RequestPrincipal(
        "supervisor", None, "tailscale-node", True, frozenset({TailCamRole.OPERATOR}),
    )
    client = TailcamClient.for_app(_app(), principal=principal)
    try:
        for path, body in [
            ("/api/ai", {"enabled": True, "model": "unapproved"}),
            ("/api/plugins/market/install", {"id": "plugin"}),
            ("/api/storage", {"media_dir": "/never-written"}),
        ]:
            with pytest.raises(TailcamMcpError) as error:
                await client.post(path, json=body)
            assert error.value.status_code == 403
    finally:
        await client.aclose()


@pytest.mark.parametrize("identity", ["local", "admin"])
def test_authorized_admin_can_install_plugin_and_change_ai(monkeypatch, identity):
    cfg = SimpleNamespace(
        ai=SimpleNamespace(
            enabled=False, model="old", base_url="http://127.0.0.1:9", provider="ollama",
        ),
        save=Mock(),
    )
    ctx = SimpleNamespace(config=cfg, market=SimpleNamespace(install=Mock()), reload_plugins=Mock())
    monkeypatch.setattr(
        routes_api, "_market_info", lambda _: PluginsMarketInfo(registry_url="https://invalid"),
    )

    async def ai_info(_):
        return AIInfo(
            enabled=cfg.ai.enabled, model=cfg.ai.model, reachable=False, model_present=False,
        )

    monkeypatch.setattr(routes_api, "_ai_info", ai_info)
    with TestClient(
        _app(ctx), base_url="http://localhost", client=("127.0.0.1", 51000),
        headers={} if identity == "local" else _headers("admin"),
    ) as client:
        installed = client.post("/api/plugins/market/install", json={"id": "curated-plugin"})
        configured = client.post("/api/ai", json={"enabled": True, "model": "approved"})
    assert installed.status_code == configured.status_code == 200
    ctx.market.install.assert_called_once_with("curated-plugin")
    ctx.reload_plugins.assert_called_once_with()
    cfg.save.assert_called_once_with()
    assert configured.json()["model"] == "approved"


@pytest.mark.parametrize("change", [
    {"media_dir": "/never-written"}, {"media_dir": ""}, {"node": ""},
    {"retention_enabled": False}, {"max_gb": 0}, {"max_age_days": 0},
])
def test_mixed_storage_body_denied_before_any_motion_or_path_effect(change):
    with TestClient(
        _app(), base_url="http://localhost", client=("127.0.0.1", 51000),
        headers=_headers("operator"),
    ) as client:
        response = client.post("/api/storage", json={"auto_record": True, **change})
    assert response.status_code == 403


def test_storage_motion_controls_remain_available_and_admin_can_change_retention(monkeypatch):
    cfg = SimpleNamespace(
        motion=SimpleNamespace(auto_record=False, record_tail_seconds=5.0),
        retention=SimpleNamespace(enabled=False, max_gb=10.0, max_age_days=30), save=Mock(),
    )
    ctx = SimpleNamespace(config=cfg)
    monkeypatch.setattr(routes_api, "_storage_info", lambda _: StorageInfo(media_dir="/fixture"))
    monkeypatch.setattr(routes_api, "_storage_nodes", AsyncMock(return_value=[]))
    with TestClient(
        _app(ctx), base_url="http://localhost", client=("127.0.0.1", 51000),
        headers=_headers("operator"),
    ) as client:
        response = client.post("/api/storage", json={
            "auto_record": True, "record_tail_seconds": 8, "media_dir": None,
        })
        assert response.status_code == 200, response.text
        assert cfg.motion.auto_record is True
        assert cfg.motion.record_tail_seconds == 8
        response = client.post(
            "/api/storage", json={"retention_enabled": True}, headers=_headers("admin"),
        )
    assert response.status_code == 200, response.text
    assert cfg.retention.enabled is True
    assert cfg.save.call_count == 2


@pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS + [
    ("POST", "/api/training/runs", {}),
    ("POST", "/api/models/1/activate", None),
    ("POST", "/api/active-learning/train", {}),
    ("PUT", "/api/samples/1/annotations", {"boxes": []}),
    ("POST", "/api/storage", {"media_dir": "", "auto_record": True}),
    ("POST", "/api//plugins/reload/", None),
    ("GET", "/api//notifications/", None),
])
def test_generic_proxy_cannot_drop_identity_for_administrative_requests(method, path, body):
    # Even a local admin cannot lend its identity to this role-stripping hop.
    # NoEffects proves denial occurs before peer discovery or network access.
    with TestClient(
        _app(), base_url="http://localhost", client=("127.0.0.1", 51000),
    ) as client:
        response = client.request(method, "/proxy/peer" + path, json=body)
    assert response.status_code == 403
    assert "directly on the destination" in response.json()["detail"]


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/cameras/camera/recording/start", {}),
    ("PATCH", "/api/cameras/camera", {"motion_enabled": True}),
    ("POST", "/api/storage", {"auto_record": True, "record_tail_seconds": 8}),
    ("GET", "/api/ai", None),
])
def test_generic_proxy_preserves_camera_and_motion_operations(method, path, body):
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b'{"ok":true}'))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as peer:
            ctx = SimpleNamespace(cluster=SimpleNamespace(
                peers=AsyncMock(), peer_base=lambda _: "http://127.0.0.1:9", client=lambda: peer,
            ))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(ctx), client=("127.0.0.1", 51000)),
                base_url="http://localhost", headers=_headers("operator"),
            ) as client:
                return await client.request(method, "/proxy/peer" + path, json=body)

    import asyncio

    response = asyncio.run(exercise())
    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0][:2] == (method, path)


def test_all_admin_legacy_routes_are_denied_by_generic_proxy():
    # Keep the proxy boundary in sync with actual route dependencies, including
    # older training/model routes and future admin-only additions in these APIs.
    for router in [routes_api.router, routes_active.router]:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            dependencies = {dependency.call for dependency in route.dependant.dependencies}
            if require_admin not in dependencies and require_storage_admin not in dependencies:
                continue
            for method in route.methods:
                assert legacy_admin_request(method, route.path), (method, route.path)
