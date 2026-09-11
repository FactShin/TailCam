"""Workload roles are explicit, durable, and never enabled by error recovery."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tailcam import paths
from tailcam.cli import app as cli
from tailcam.cluster.service import ClusterService
from tailcam.config import AppConfig, NodeConfig, PeersConfig
from tailcam.management.capabilities import NodeCapabilityService
from tailcam.management.health import NodeHealthService
from tailcam.node import ROLE_NAMES, ROLE_PRESETS, NodeConfigError
from tailcam.persistence.models import CameraRecord
from tailcam.persistence.store import Store
from tailcam.tailscale.client import TailscaleClient, TailscaleStatus
from tailcam.web import routes_node_v1
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context


def test_legacy_defaults_and_hub_round_trip(isolated_env):
    legacy = AppConfig.from_dict({"server": {"port": 9012}})
    assert legacy.node.roles == list(ROLE_NAMES)
    legacy.node = NodeConfig(name="Control desk", roles=[])
    legacy.save()
    restored = AppConfig.load()
    assert restored.node.name == "Control desk"
    assert restored.node.roles == []
    assert restored.server.port == 9012
    assert AppConfig().node.roles == list(ROLE_NAMES)


@pytest.mark.parametrize(
    "node",
    [
        None,
        [],
        "hub",
        {"roles": None},
        {"roles": "capture"},
        {"roles": ["captuer"]},
        {"roles": ["capture", "capture"]},
        {"roles": [True]},
        {"roles": [{}]},
        {"name": 1},
        {"name": "x" * 65},
        {"name": "unsafe\nname"},
        {"role": "capture"},
        {"node_id": str(uuid4())},
    ],
)
def test_invalid_node_configuration_fails_closed(node):
    with pytest.raises(NodeConfigError):
        AppConfig.from_dict({"node": node})


@pytest.mark.parametrize(
    "content",
    [
        '[node]\nroles = ["captuer"]\n',
        '[node]\nroles = ["capture", "capture"]\n',
        '[node]\nroles = "hub"\n',
        "[node]\nroles = [\n",
        '[node]\nroles = []\n[server]\nport = "unterminated\n',
    ],
)
def test_invalid_file_is_preserved_without_default_recovery(isolated_env, content):
    path = paths.config_file()
    path.write_text(content)
    with pytest.raises(NodeConfigError, match="file was preserved"):
        AppConfig.load()
    assert path.read_text() == content
    assert not path.with_suffix(".toml.bad").exists()


def test_unreadable_config_cannot_enable_defaults(isolated_env, monkeypatch):
    path = paths.config_file()
    AppConfig(node=NodeConfig(roles=[])).save()
    original = path.read_bytes()
    from pathlib import Path

    open_original = Path.open

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError("denied")
        return open_original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", denied)
        with pytest.raises(NodeConfigError, match="No default workloads"):
            AppConfig.load()
    assert path.read_bytes() == original


def test_node_identity_survives_restart_and_config_changes(store):
    node_id = store.get_node_id()
    assert UUID(node_id).version == 4
    config = AppConfig(node=NodeConfig(name="Renamed", roles=[]))
    config.save()
    assert Store(store.db_path).get_node_id() == node_id
    assert "node_id" not in config.to_dict()["node"]


def test_node_identity_has_one_winner_under_concurrent_startup(store):
    stores = [Store(store.db_path) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        identities = list(executor.map(lambda db: db.get_node_id(), stores))
    assert len(set(identities)) == 1
    assert store._conn().execute("SELECT count(*) FROM node_identity").fetchone()[0] == 1


def test_schema_12_upgrade_preserves_existing_records(store):
    record = CameraRecord("old-camera", "Old camera", "synthetic", "{}", 1.0)
    store.upsert_camera(record)
    with store._conn() as connection:
        connection.execute("DROP TABLE node_identity")
        connection.execute("UPDATE schema_version SET version=12")
    upgraded = Store(store.db_path)
    assert upgraded.get_camera("old-camera") == record
    assert UUID(upgraded.get_node_id()).version == 4
    assert upgraded._conn().execute("SELECT version FROM schema_version").fetchone()[0] == 13


@pytest.fixture
def node_app(context):
    # No lifespan, live peers, or analyzer activity is needed for these APIs.
    app = FastAPI()
    app.include_router(routes_node_v1.router)
    app.dependency_overrides[get_context] = lambda: context
    return app


@pytest.fixture
def node_client(node_app):
    with TestClient(
        node_app, base_url="http://localhost:8088", client=("127.0.0.1", 50000)
    ) as client:
        yield client


def test_node_patch_persists_pending_roles_without_changing_live_workers(node_client, context):
    original = node_client.get("/api/v1/node/config").json()
    response = node_client.patch("/api/v1/node/config", json={"name": " Hub ", "roles": []})
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed == {
        "node_id": original["node_id"],
        "name": "Hub",
        "configured_roles": [],
        "active_roles": list(ROLE_NAMES),
        "restart_required": True,
    }
    assert context.active_roles == frozenset(ROLE_NAMES)
    assert AppConfig.load().node == NodeConfig(name="Hub", roles=[])
    # Omitting roles during a rename must keep an explicitly empty hub role set.
    assert (
        node_client.patch("/api/v1/node/config", json={"name": "Desk"}).json()["configured_roles"]
        == []
    )
    audit = node_client.get("/api/v1/node/audit").json()[0]
    assert audit["action"] == "node.config" and audit["result"] == "success"
    assert audit["target"] == original["node_id"]
    restored = AppConfig.load()
    restarted = AppContext(restored, store=context.store)
    try:
        assert routes_node_v1.node_config(restarted).restart_required is False
        assert restarted.active_roles == frozenset()
        assert restarted.node_id == original["node_id"]
    finally:
        restarted.shutdown()


@pytest.mark.parametrize(
    "body",
    [
        {"node_id": str(uuid4())},
        {"roles": None},
        {"roles": "hub"},
        {"roles": ["unknown"]},
        {"roles": ["capture", "capture"]},
        {"name": None},
        {"name": "x" * 65},
        {"name": "bad\x00name"},
        {"unexpected": True},
    ],
)
def test_node_patch_rejects_invalid_fields_without_mutation(node_client, context, body):
    original = context.config.node
    response = node_client.patch("/api/v1/node/config", json=body)
    assert response.status_code == 422
    assert context.config.node is original


def test_unverified_node_patch_requires_admin(node_app, context):
    with TestClient(
        node_app, base_url="http://localhost:8088", client=("100.64.0.22", 50000)
    ) as client:
        response = client.patch("/api/v1/node/config", json={"roles": []})
    assert response.status_code == 403
    assert context.config.node.roles == list(ROLE_NAMES)


def test_failed_node_save_preserves_disk_and_live_config(node_client, context, monkeypatch):
    context.config.save()
    original = context.config.node
    disk = paths.config_file().read_bytes()

    def fail_save(*args, **kwargs):
        raise OSError("simulated full disk")

    monkeypatch.setattr(AppConfig, "save", fail_save)
    response = node_client.patch("/api/v1/node/config", json={"roles": []})
    assert response.status_code == 503
    assert context.config.node is original
    assert paths.config_file().read_bytes() == disk
    assert node_client.get("/api/v1/node/audit").json()[0]["result"] == "failure"


def test_hub_health_and_capabilities_do_not_probe_ai(store, monkeypatch):
    config = AppConfig(node=NodeConfig(roles=[]))
    config.ai.enabled = True
    config.ai.base_url = "http://127.0.0.1:1"
    config.peers.auto_discover = False
    context = AppContext(config, store=store)
    monkeypatch.setattr(
        context.tailscale, "status", lambda: TailscaleStatus(False, False, None, None)
    )
    monkeypatch.setattr(
        context.analyzer, "health", lambda: pytest.fail("Disabled analysis was probed")
    )
    try:
        health = NodeHealthService(
            context, update_checker=lambda **kw: ("1", None, False)
        ).snapshot()
        assert health.ai_enabled is False and health.ai_reachable is False
        assert health.camera_total == 0
        assert not any(issue.code.startswith("ai.") for issue in health.issues)
        capabilities = NodeCapabilityService(context).snapshot()
        assert not any(
            cap.startswith("camera.") or cap.startswith("ai.") for cap in capabilities.capabilities
        )
        assert capabilities.node_roles == ()
        assert capabilities.node_id == context.node_id
    finally:
        context.shutdown()


@pytest.mark.parametrize("preset, expected", list(ROLE_PRESETS.items()))
def test_cli_presets_round_trip(isolated_env, preset, expected):
    result = CliRunner().invoke(cli, ["config", "--preset", preset, "--node-name", "Test node"])
    assert result.exit_code == 0, result.output
    assert AppConfig.load().node == NodeConfig(name="Test node", roles=list(expected))


@pytest.mark.parametrize("mode", ["--init", "--reset"])
def test_cli_initialization_publishes_requested_roles_in_one_write(isolated_env, monkeypatch, mode):
    if mode == "--reset":
        AppConfig(node=NodeConfig(roles=["storage"])).save()
    writes = []
    original_save = AppConfig.save

    def track_save(config, *args, **kwargs):
        writes.append((list(config.node.roles), config.node.name, config.server.port))
        return original_save(config, *args, **kwargs)

    monkeypatch.setattr(AppConfig, "save", track_save)
    result = CliRunner().invoke(
        cli, ["config", mode, "--preset", "hub", "--node-name", "Desk", "--port", "9012"],
    )
    assert result.exit_code == 0, result.output
    # A default all-role file followed by a corrected hub file is unsafe:
    # startup can race the first write, or the second write can fail.
    assert writes == [([], "Desk", 9012)]
    assert AppConfig.load().node == NodeConfig(name="Desk", roles=[])


def test_cli_omitted_roles_preserve_hub_and_invalid_roles_do_not_write(isolated_env):
    runner = CliRunner()
    assert runner.invoke(cli, ["config", "--roles", ""]).exit_code == 0
    assert runner.invoke(cli, ["config", "--port", "9012"]).exit_code == 0
    assert AppConfig.load().node.roles == []
    original = paths.config_file().read_bytes()
    for arguments in (
        ["--roles", "unknown"],
        ["--roles", "capture,capture"],
        ["--roles", "capture", "--preset", "hub"],
    ):
        assert runner.invoke(cli, ["config", *arguments]).exit_code != 0
        assert paths.config_file().read_bytes() == original


@pytest.mark.parametrize("command", ["status", "doctor", "cameras"])
def test_cli_skips_capture_hardware_when_disabled(isolated_env, monkeypatch, command):
    config = AppConfig(node=NodeConfig(roles=[]))
    config.peers.auto_discover = False
    config.save()
    from tailcam.camera import enumerate as camera_enumerate

    monkeypatch.setattr(
        camera_enumerate, "discover", lambda: pytest.fail("Camera hardware was probed")
    )
    monkeypatch.setattr(
        TailscaleClient, "status", lambda self: TailscaleStatus(False, False, None, None)
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(503))
    result = CliRunner().invoke(cli, [command])
    assert result.exit_code == 0, result.output
    assert "disabled" in result.output


@pytest.mark.parametrize(
    "advertised, expected",
    [
        ({}, {"node_id": None, "node_name": None, "node_roles": None}),
        (
            {"node_id": "bad", "node_name": {}, "node_roles": ["unknown"]},
            {"node_id": None, "node_name": None, "node_roles": None},
        ),
        (
            {"node_name": "Hub", "node_roles": []},
            {"node_id": None, "node_name": "Hub", "node_roles": []},
        ),
    ],
)
def test_peer_metadata_is_additive_and_untrusted(advertised, expected):
    async def probe():
        cluster = ClusterService(PeersConfig(auto_discover=False), TailscaleClient(), "local")
        cluster._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"version": "1.9.0", "host": "peer", **advertised}
                )
            )
        )
        try:
            peer = await cluster._probe("http://peer:8088")
            assert peer is not None
            assert {key: getattr(peer, key) for key in expected} == expected
            assert peer.key == "peer"  # UUIDs do not change existing proxy routing.
        finally:
            await cluster.aclose()

    asyncio.run(probe())


@pytest.mark.parametrize(
    "fields",
    [
        {"host": ["peer"]},
        {"host": {}},
        {"host": False},
        {"host": "peer\nname"},
        {"host": "x" * 254},
        {"version": ["1.9.0"]},
        {"version": None},
    ],
)
def test_malformed_peer_identity_is_skipped_without_breaking_discovery(fields):
    async def refresh():
        cluster = ClusterService(
            PeersConfig(auto_discover=False, static=["http://bad:8088", "http://good:8088"]),
            TailscaleClient(),
            "local",
        )
        cluster._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "version": "1.9.0",
                        "host": "good" if request.url.host == "good" else "bad",
                        **(fields if request.url.host == "bad" else {}),
                    },
                )
            )
        )
        try:
            assert [peer.host for peer in await cluster.refresh(force=True)] == ["good"]
        finally:
            await cluster.aclose()

    asyncio.run(refresh())
