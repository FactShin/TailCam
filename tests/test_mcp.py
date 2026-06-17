from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tailcam.config import AppConfig
from tailcam.management.audit import AuditLog
from tailcam.mcp import errors
from tailcam.mcp.client import TailcamClient
from tailcam.mcp.errors import TailcamMcpError
from tailcam.mcp.server import McpServer
from tailcam.mcp.transport_stdio import _LOCAL_PRINCIPAL
from tailcam.security.principal import RequestPrincipal, TailCamRole
from tailcam.web.app import create_app

ADMIN = RequestPrincipal(
    "alice", "Alice", "tailscale-user", True,
    frozenset({TailCamRole.VIEWER, TailCamRole.OPERATOR, TailCamRole.ADMIN}),
)
VIEWER = RequestPrincipal("node", None, "tailscale-node", True, frozenset({TailCamRole.VIEWER}))
UNVERIFIED = RequestPrincipal("anon", None, "unverified", False, frozenset())


@pytest.fixture
def mcp_env(context):
    # http_enabled so the /mcp route is mounted; TestClient runs lifespan startup
    # so the synthetic camera worker produces frames for snapshot/recording.
    context.config.mcp.http_enabled = True
    app = create_app(context.config, context=context)
    with TestClient(app):
        yield app, context


def _server(app, principal, *, audit_store=None) -> tuple[McpServer, TailcamClient]:
    client = TailcamClient.for_app(app)
    audit = AuditLog(audit_store) if audit_store is not None else None
    server = McpServer(
        client=client,
        principal=principal,
        config=app.state.ctx.config.mcp,
        transport="streamable_http",
        audit=audit,
    )
    return server, client


async def _call(server, method, **params):
    return await server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


# -- protocol / registry ---------------------------------------------------
async def test_initialize_reports_protocol_and_server(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "initialize", clientInfo={"name": "pytest"})
    finally:
        await client.aclose()
    result = res["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "tailcam"
    assert "Tailscale" in result["instructions"]
    assert server.client_name == "pytest"


async def test_tools_list_filters_by_role(mcp_env):
    app, _ = mcp_env
    admin_server, ac = _server(app, ADMIN)
    viewer_server, vc = _server(app, VIEWER)
    try:
        admin_list = (await _call(admin_server, "tools/list"))["result"]["tools"]
        viewer_list = (await _call(viewer_server, "tools/list"))["result"]["tools"]
        admin_names = {t["name"] for t in admin_list}
        viewer_names = {t["name"] for t in viewer_list}
    finally:
        await ac.aclose()
        await vc.aclose()
    assert "reload_node" in admin_names and "get_audit_log" in admin_names
    assert "reload_node" not in viewer_names and "get_audit_log" not in viewer_names
    assert "list_cameras" in viewer_names  # read tools stay visible
    assert viewer_names < admin_names


async def test_unknown_tool_is_jsonrpc_error(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "tools/call", name="nope", arguments={})
    finally:
        await client.aclose()
    assert res["error"]["code"] == -32602


async def test_unknown_method_is_method_not_found(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "does/not/exist")
    finally:
        await client.aclose()
    assert res["error"]["code"] == -32601


# -- read tools ------------------------------------------------------------
async def test_get_system_status(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "tools/call", name="get_system_status", arguments={})
    finally:
        await client.aclose()
    body = res["result"]
    assert body["isError"] is False
    assert body["structuredContent"]["system"]["version"]


async def test_list_cameras_returns_synthetic(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "tools/call", name="list_cameras", arguments={"scope": "local"})
    finally:
        await client.aclose()
    cams = res["result"]["structuredContent"]["cameras"]
    assert len(cams) >= 1


# -- resources / prompts ---------------------------------------------------
async def test_resource_read_system(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "resources/read", uri="tailcam://system")
    finally:
        await client.aclose()
    contents = res["result"]["contents"]
    assert contents[0]["mimeType"] == "application/json"
    assert "version" in contents[0]["text"]


async def test_resource_unknown_uri_errors(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "resources/read", uri="tailcam://bogus")
    finally:
        await client.aclose()
    assert "error" in res


async def test_audit_resource_requires_admin(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, VIEWER)
    try:
        res = await _call(server, "resources/read", uri="tailcam://audit/recent")
    finally:
        await client.aclose()
    assert res["error"]["data"]["code"] == errors.ADMIN_REQUIRED


async def test_prompts_list_and_get(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        listing = await _call(server, "prompts/list")
        got = await _call(server, "prompts/get", name="tailcam_motion_investigation",
                          arguments={"event_id": 7})
    finally:
        await client.aclose()
    names = {p["name"] for p in listing["result"]["prompts"]}
    assert "tailcam_fleet_triage" in names
    assert "#7" in got["result"]["messages"][0]["content"]["text"]


# -- authorization / confirmation -----------------------------------------
async def test_reload_requires_confirm_scope(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "tools/call", name="reload_node", arguments={"node_key": "local"})
    finally:
        await client.aclose()
    body = res["result"]
    assert body["isError"] is True
    assert body["structuredContent"]["error"]["code"] == errors.CONFIRMATION_REQUIRED


async def test_admin_tool_denied_for_viewer(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, VIEWER)
    try:
        res = await _call(server, "tools/call", name="get_audit_log", arguments={})
    finally:
        await client.aclose()
    assert res["result"]["structuredContent"]["error"]["code"] == errors.ADMIN_REQUIRED


async def test_restart_camera_requires_confirm(mcp_env):
    app, ctx = mcp_env
    server, client = _server(app, ADMIN)
    cam_id = ctx.manager.list()[0].descriptor.id
    try:
        res = await _call(server, "tools/call", name="restart_camera",
                          arguments={"camera_id": cam_id})
    finally:
        await client.aclose()
    assert res["result"]["structuredContent"]["error"]["code"] == errors.CONFIRMATION_REQUIRED


# -- writes audit ----------------------------------------------------------
async def test_capture_snapshot_records_audit(mcp_env):
    app, ctx = mcp_env
    server, client = _server(app, ADMIN, audit_store=ctx.store)
    cam_id = ctx.manager.list()[0].descriptor.id
    try:
        res = await _call(server, "tools/call", name="capture_snapshot",
                          arguments={"camera_id": cam_id})
    finally:
        await client.aclose()
    assert res["result"]["isError"] is False
    actions = [r.action for r in AuditLog(ctx.store).list()]
    assert "mcp.capture_snapshot" in actions


# -- client error normalization -------------------------------------------
async def test_client_normalizes_unknown_camera(mcp_env):
    app, _ = mcp_env
    client = TailcamClient.for_app(app)
    try:
        with pytest.raises(TailcamMcpError) as excinfo:
            await client.camera("no-such-camera")
    finally:
        await client.aclose()
    assert excinfo.value.code == errors.CAMERA_UNKNOWN
    assert excinfo.value.status_code == 404


async def test_camera_id_path_traversal_rejected(mcp_env):
    # A camera id with a traversal segment must not collapse into another
    # endpoint (httpx removes dot-segments when joining paths). The MCP role gate
    # would otherwise be bypassed because in-process calls run as loopback-admin.
    app, _ = mcp_env
    client = TailcamClient.for_app(app)
    try:
        with pytest.raises(TailcamMcpError) as excinfo:
            await client.camera("../v1/node/audit")
    finally:
        await client.aclose()
    assert excinfo.value.code == errors.CAMERA_UNKNOWN


async def test_viewer_cannot_reach_audit_via_camera_resource(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, VIEWER)
    try:
        res = await _call(server, "resources/read", uri="tailcam://cameras/../v1/node/audit")
    finally:
        await client.aclose()
    # Routed to the camera handler (or rejected) — never the admin audit endpoint.
    assert "error" in res


# -- HTTP transport --------------------------------------------------------
async def test_http_mount_absent_when_disabled(context):
    context.config.mcp.http_enabled = False
    app = create_app(context.config, context=context)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://t.local") as c:
        resp = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    # No POST /mcp handler exists: 404, or 405 when a GET catch-all owns the path.
    assert resp.status_code in (404, 405)


async def test_http_rejects_unverified(mcp_env):
    app, _ = mcp_env
    # Non-loopback client => principal unverified => 401 before any tool runs.
    transport = httpx.ASGITransport(app=app, client=("9.9.9.9", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://t.local") as c:
        resp = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401


async def test_http_initialize_with_tailscale_identity(mcp_env):
    app, _ = mcp_env
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://t.local") as c:
        resp = await c.post(
            "/mcp",
            headers={"tailscale-user-login": "alice@example.com"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "tailcam"


async def test_http_notification_returns_202(mcp_env):
    app, _ = mcp_env
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://t.local") as c:
        resp = await c.post(
            "/mcp",
            headers={"tailscale-user-login": "alice@example.com"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
    assert resp.status_code == 202


async def test_http_get_not_allowed(mcp_env):
    app, _ = mcp_env
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://t.local") as c:
        resp = await c.get("/mcp", headers={"tailscale-user-login": "alice@example.com"})
    assert resp.status_code == 405


# -- cli / config / stdio --------------------------------------------------
def test_cli_mcp_command_registered():
    from tailcam.cli import app as cli_app

    result = CliRunner().invoke(cli_app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "stdio" in result.stdout


def test_stdio_principal_is_local_admin():
    assert _LOCAL_PRINCIPAL.verified
    assert TailCamRole.ADMIN in _LOCAL_PRINCIPAL.roles


def test_config_mcp_roundtrips():
    cfg = AppConfig()
    data = cfg.to_dict()
    assert "mcp" in data
    restored = AppConfig.from_dict(data)
    assert restored.mcp.enabled is True
    assert restored.mcp.require_confirm_for_fleet_writes is True
