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


# -- granular AI / training control ---------------------------------------
async def test_dataset_lifecycle_and_audit(mcp_env):
    app, ctx = mcp_env
    server, client = _server(app, ADMIN, audit_store=ctx.store)
    try:
        created = await _call(server, "tools/call", name="create_dataset",
                              arguments={"name": "agents", "task": "classification"})
        ds_id = created["result"]["structuredContent"]["dataset"]["id"]
        got = await _call(server, "tools/call", name="get_dataset",
                          arguments={"dataset_id": ds_id})
        deleted = await _call(server, "tools/call", name="delete_dataset",
                              arguments={"dataset_id": ds_id, "confirm": True})
    finally:
        await client.aclose()
    assert created["result"]["isError"] is False
    assert got["result"]["structuredContent"]["dataset"]["id"] == ds_id
    assert deleted["result"]["structuredContent"]["ok"] is True
    actions = {r.action for r in AuditLog(ctx.store).list()}
    assert {"mcp.create_dataset", "mcp.delete_dataset"} <= actions


async def test_delete_dataset_requires_confirm(mcp_env):
    app, ctx = mcp_env
    server, client = _server(app, ADMIN)
    try:
        created = await _call(server, "tools/call", name="create_dataset",
                              arguments={"name": "x"})
        ds_id = created["result"]["structuredContent"]["dataset"]["id"]
        res = await _call(server, "tools/call", name="delete_dataset",
                          arguments={"dataset_id": ds_id})
    finally:
        await client.aclose()
    assert res["result"]["structuredContent"]["error"]["code"] == errors.CONFIRMATION_REQUIRED


async def test_start_training_run_confirm_then_engine_state(mcp_env):
    app, ctx = mcp_env
    server, client = _server(app, ADMIN)
    try:
        created = await _call(server, "tools/call", name="create_dataset",
                              arguments={"name": "train"})
        ds_id = created["result"]["structuredContent"]["dataset"]["id"]
        no_confirm = await _call(server, "tools/call", name="start_training_run",
                                 arguments={"dataset_id": ds_id})
        confirmed = await _call(server, "tools/call", name="start_training_run",
                                arguments={"dataset_id": ds_id, "confirm": True})
    finally:
        await client.aclose()
    no_confirm_code = no_confirm["result"]["structuredContent"]["error"]["code"]
    assert no_confirm_code == errors.CONFIRMATION_REQUIRED
    # Engine isn't installed in CI: a clean normalized error, not a crash.
    assert confirmed["result"]["isError"] is True
    assert confirmed["result"]["structuredContent"]["error"]["code"] in (
        errors.INVALID_RESPONSE, errors.NOT_RUNNING
    )


async def test_training_tools_denied_for_viewer(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, VIEWER)
    try:
        res = await _call(server, "tools/call", name="create_dataset", arguments={"name": "x"})
    finally:
        await client.aclose()
    assert res["result"]["structuredContent"]["error"]["code"] == errors.ADMIN_REQUIRED


async def test_list_ollama_models_when_unreachable(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "tools/call", name="list_ollama_models", arguments={})
    finally:
        await client.aclose()
    ollama = res["result"]["structuredContent"]["ollama"]
    assert ollama["reachable"] is False
    assert ollama["installed"] == []


async def test_pull_ollama_model_unreachable_is_peer_error(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        res = await _call(server, "tools/call", name="pull_ollama_model",
                          arguments={"model": "moondream", "confirm": True})
    finally:
        await client.aclose()
    assert res["result"]["structuredContent"]["error"]["code"] == errors.PEER_UNREACHABLE


async def test_model_lifecycle_tools_present(mcp_env):
    app, _ = mcp_env
    server, client = _server(app, ADMIN)
    try:
        names = {t["name"] for t in (await _call(server, "tools/list"))["result"]["tools"]}
    finally:
        await client.aclose()
    expected = {
        "list_ollama_models", "pull_ollama_model", "load_ollama_model",
        "create_dataset", "delete_dataset", "get_dataset", "list_dataset_samples",
        "relabel_sample", "delete_sample", "list_models", "register_model",
        "activate_model", "deactivate_model", "delete_model",
        "start_training_run", "list_training_runs", "get_training_run", "stop_training_run",
    }
    assert expected <= names


# -- new REST endpoints ----------------------------------------------------
def test_rest_ai_models_endpoint(client):
    resp = client.get("/api/ai/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body["active_model"]


def test_rest_ai_pull_unreachable_returns_502(client):
    resp = client.post("/api/ai/pull", json={"model": "moondream"})
    assert resp.status_code == 502


def test_rest_ai_pull_progress_idle(client):
    resp = client.get("/api/ai/pull")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is False
    assert body["status"] == "idle"
    assert body["percent"] == 0.0


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
async def test_http_disabled_gates_both_verbs(context):
    # The /mcp router is always mounted now; the runtime gate must fail-closed
    # for BOTH verbs when http is disabled, so a disabled node doesn't advertise
    # the endpoint (GET used to leak 405 Allow:POST here).
    context.config.mcp.http_enabled = False
    app = create_app(context.config, context=context)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://node.ts.net") as c:
        post = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        get = await c.get("/mcp", headers={"accept": "application/json"})
    assert post.status_code == 404
    assert "disabled" in post.json()["error"]["message"]
    assert get.status_code == 404  # not 405 — the endpoint stays hidden


async def test_mcp_get_redirects_browsers_to_page(context):
    # A human typing /mcp (Accept: text/html) gets sent to the SPA settings tab
    # instead of a bare protocol response — even when http is disabled.
    context.config.mcp.http_enabled = False
    app = create_app(context.config, context=context)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://node.ts.net") as c:
        resp = await c.get("/mcp", headers={"accept": "text/html"})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/agents"


async def test_http_rejects_unverified(mcp_env):
    app, _ = mcp_env
    # Non-loopback client => principal unverified => 401 before any tool runs.
    transport = httpx.ASGITransport(app=app, client=("9.9.9.9", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://node.ts.net") as c:
        resp = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401


async def test_http_initialize_with_tailscale_identity(mcp_env):
    app, _ = mcp_env
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://node.ts.net") as c:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://node.ts.net") as c:
        resp = await c.post(
            "/mcp",
            headers={"tailscale-user-login": "alice@example.com"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
    assert resp.status_code == 202


async def test_http_get_not_allowed(mcp_env):
    app, _ = mcp_env
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://node.ts.net") as c:
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


# -- /api/mcp status + live HTTP gate (1.1.0: MCP page) ----------------------
def test_api_mcp_info_and_toggle(client):
    info = client.get("/api/mcp").json()
    assert info["enabled"] is True
    assert info["http_enabled"] is False  # network endpoint is opt-in
    assert info["http_live"] is False
    assert info["stdio_command"] == "tailcam mcp stdio"
    assert info["stdio_args"] == ["mcp", "stdio"]
    assert info["recommended_tools"]  # non-empty starter set for autoEnableTools
    assert info["tools_count"] >= 40
    assert info["http_url_local"].endswith("/mcp")
    assert info["tailcam_url"].startswith("http://127.0.0.1:")

    on = client.post("/api/mcp", json={"http_enabled": True}).json()
    assert on["http_enabled"] is True and on["http_live"] is True
    off = client.post("/api/mcp", json={"enabled": False}).json()
    assert off["http_live"] is False


def test_example_config_autoenabletools_matches_constant():
    # The static examples/mcp/hermes-openclaw.json and the RECOMMENDED_TOOLS
    # constant the MCP page renders must not drift.
    import json
    from pathlib import Path

    from tailcam.mcp import RECOMMENDED_TOOLS

    root = Path(__file__).resolve().parent.parent
    example = json.loads((root / "examples" / "mcp" / "hermes-openclaw.json").read_text())
    tools = example["mcpServers"]["tailcam"]["autoEnableTools"]
    assert tools == RECOMMENDED_TOOLS


def test_mcp_http_gate_is_live_no_restart(client):
    # Disabled by default: the route exists but answers 404 with a JSON-RPC error.
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = client.post("/mcp", json=init)
    assert resp.status_code == 404
    assert "disabled" in resp.json()["error"]["message"]

    # Flip the toggle through the API — the same app instance starts answering
    # (fail-closed auth still applies: an unverified caller gets 401, not 404).
    client.post("/api/mcp", json={"http_enabled": True})
    resp = client.post("/mcp", json=init)
    assert resp.status_code == 401

    # And off again, immediately.
    client.post("/api/mcp", json={"http_enabled": False})
    assert client.post("/mcp", json=init).status_code == 404
