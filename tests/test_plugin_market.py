"""1.0: plugin marketplace (verify-then-install), SDK settings, event hooks,
disabled enforcement, hot reload, REST endpoints."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from tailcam.config import AppConfig, PluginsConfig
from tailcam.plugins import sdk
from tailcam.plugins.market import MarketError, PluginMarket
from tailcam.plugins.registry import PluginRegistry

GOOD_PLUGIN = b'''"""A test plugin."""
from tailcam.plugins.sdk import PluginInfo, hookimpl

@hookimpl
def tailcam_plugin_info():
    return [PluginInfo(id="testplug", name="Test plug", kind="other")]
'''


def _index(entries: list[dict]) -> dict:
    return {"schema_version": 1, "plugins": entries}


def _entry(payload: bytes = GOOD_PLUGIN, **over) -> dict:
    entry = {
        "id": "testplug",
        "name": "Test plug",
        "version": "1.0.0",
        "description": "a test",
        "file": "testplug.py",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "url": "https://registry.test/plugins/testplug.py",
        "kinds": ["other"],
    }
    entry.update(over)
    return entry


@pytest.fixture
def market(isolated_env, monkeypatch):
    """A PluginMarket wired to a fake HTTPS registry served from memory."""
    cfg = PluginsConfig(registry_url="https://registry.test/index.json")
    m = PluginMarket(cfg)
    files: dict[str, bytes] = {
        "https://registry.test/index.json": json.dumps(_index([_entry()])).encode(),
        "https://registry.test/plugins/testplug.py": GOOD_PLUGIN,
    }

    def fake_get(url, **kw):
        if url not in files:
            raise httpx.ConnectError(f"no route to {url}")
        return httpx.Response(200, content=files[url], request=httpx.Request("GET", url))

    monkeypatch.setattr("tailcam.plugins.market.httpx.get", fake_get)
    m._files = files  # type: ignore[attr-defined] - test handle
    m._cfg = cfg  # type: ignore[attr-defined]
    return m


# ------------------------------------------------------------------ catalog
def test_catalog_parses_registry(market):
    plugins, error = market.catalog()
    assert error == ""
    assert [p.id for p in plugins] == ["testplug"]
    assert plugins[0].url == "https://registry.test/plugins/testplug.py"


def test_catalog_requires_https(isolated_env):
    m = PluginMarket(PluginsConfig(registry_url="http://registry.test/index.json"))
    plugins, error = m.catalog()
    assert plugins == [] and "https" in error


def test_catalog_rejects_bad_entries(market):
    bad_cases = [
        _entry(id="../evil"),
        _entry(file="../../evil.py"),
        _entry(sha256="nothex"),
        _entry(url="http://plain.test/x.py"),
    ]
    for bad in bad_cases:
        market._files["https://registry.test/index.json"] = json.dumps(_index([bad])).encode()
        plugins, error = market.catalog(force=True)
        assert plugins == [] and "invalid" in error, bad


def test_catalog_serves_stale_cache_on_outage(market):
    plugins, _ = market.catalog()
    assert plugins
    del market._files["https://registry.test/index.json"]
    plugins2, error = market.catalog(force=True)
    assert [p.id for p in plugins2] == ["testplug"]  # stale cache still served
    assert "unreachable" in error


# ------------------------------------------------------------------ install
def test_install_writes_file_and_sidecar(market):
    from tailcam.plugins.registry import dropin_dir

    installed = market.install("testplug")
    assert installed.source == "market" and installed.version == "1.0.0"
    dest = dropin_dir() / "testplug.py"
    assert dest.read_bytes() == GOOD_PLUGIN
    meta = json.loads((dropin_dir() / "testplug.py.meta.json").read_text())
    assert meta["market_id"] == "testplug"
    assert meta["sha256"] == hashlib.sha256(GOOD_PLUGIN).hexdigest()


def test_install_rejects_checksum_mismatch(market):
    from tailcam.plugins.registry import dropin_dir

    market._files["https://registry.test/plugins/testplug.py"] = b"print('tampered')\n"
    with pytest.raises(MarketError, match="checksum"):
        market.install("testplug")
    assert not (dropin_dir() / "testplug.py").exists()  # nothing partial on disk


def test_install_rejects_invalid_python(market):
    payload = b"def broken(:\n"
    market._files["https://registry.test/index.json"] = json.dumps(
        _index([_entry(payload)])
    ).encode()
    market._files["https://registry.test/plugins/testplug.py"] = payload
    with pytest.raises(MarketError, match="not valid Python"):
        market.install("testplug")


def test_install_rejects_oversized_file(market, monkeypatch):
    monkeypatch.setattr("tailcam.plugins.market._MAX_PLUGIN_BYTES", 10)
    with pytest.raises(MarketError, match="1 MB|limit"):
        market.install("testplug")


def test_install_unknown_id(market):
    with pytest.raises(MarketError, match="not in the registry"):
        market.install("nope")


def test_uninstall_and_guards(market):
    from tailcam.plugins.registry import dropin_dir

    market.install("testplug")
    assert market.uninstall("testplug") is True
    assert not (dropin_dir() / "testplug.py").exists()
    assert not (dropin_dir() / "testplug.py.meta.json").exists()
    assert market.uninstall("testplug") is False  # already gone
    with pytest.raises(MarketError):
        market.uninstall("../etc/passwd")


def test_installed_reports_update_available(market):
    market.install("testplug")
    newer = json.dumps(_index([_entry(version="2.0.0")])).encode()
    market._files["https://registry.test/index.json"] = newer
    market.catalog(force=True)
    entries = market.installed()
    assert entries[0].update_available == "2.0.0"
    assert entries[0].source == "market"


# ---------------------------------------------------------- registry + sdk
def test_registry_loads_dropin_and_event_hooks(market):
    from tailcam.plugins.registry import dropin_dir

    market.install("testplug")
    reg = PluginRegistry(disabled=[], dropin_dir=dropin_dir())
    assert "testplug" in reg.loaded_dropins
    assert any(i.id == "testplug" for i in reg.plugin_infos())


def test_registry_skips_disabled_dropin(market):
    from tailcam.plugins.registry import dropin_dir

    market.install("testplug")
    reg = PluginRegistry(disabled=["testplug"], dropin_dir=dropin_dir())
    assert "testplug" not in reg.loaded_dropins
    assert "testplug" in reg.skipped_dropins
    assert not any(i.id == "testplug" for i in reg.plugin_infos())


def test_sdk_plugin_settings_roundtrip():
    config = AppConfig()
    config.plugins.settings = {"ntfy": {"topic": "alerts"}}
    sdk._set_config(config)
    try:
        assert sdk.plugin_settings("ntfy") == {"topic": "alerts"}
        assert sdk.plugin_settings("missing") == {}
    finally:
        sdk._set_config(config)  # leave a valid config registered


def test_plugin_settings_persist_in_toml(tmp_path):
    cfg_file = tmp_path / "config.toml"
    config = AppConfig()
    config.plugins.settings = {"slack": {"webhook_url": "https://hooks.slack.test/x"}}
    config.save(cfg_file)
    loaded = AppConfig.load(cfg_file)
    assert loaded.plugins.settings["slack"]["webhook_url"] == "https://hooks.slack.test/x"


def test_event_hooks_fire_on_motion(context):
    from tailcam.plugins.hookspecs import hookimpl

    seen = []

    class Hook:
        id = "t"
        name = "t"

        def on_motion(self, event):
            seen.append(event)

    class Plug:
        @hookimpl
        def tailcam_event_hooks(self):
            return [Hook()]

    context.plugins._pm.register(Plug(), name="test.hook")
    context._motion_fanout.notify_motion(
        camera_id="cam1", label="person", confidence=0.9,
        description="", event_id=1, image_path=None,
    )
    assert len(seen) == 1
    assert seen[0].camera_id == "cam1" and seen[0].label == "person"


def test_event_hook_failure_never_raises(context):
    from tailcam.plugins.hookspecs import hookimpl

    class Hook:
        id = "boom"
        name = "boom"

        def on_motion(self, event):
            raise RuntimeError("kaboom")

    class Plug:
        @hookimpl
        def tailcam_event_hooks(self):
            return [Hook()]

    context.plugins._pm.register(Plug(), name="test.boom")
    context._motion_fanout.notify_motion(
        camera_id="cam1", label=None, confidence=None,
        description=None, event_id=None, image_path=None,
    )  # must not raise


# ------------------------------------------------------------------ REST API
def _wire_fake_registry(client, monkeypatch):
    files = {
        "https://registry.test/index.json": json.dumps(_index([_entry()])).encode(),
        "https://registry.test/plugins/testplug.py": GOOD_PLUGIN,
    }

    def fake_get(url, **kw):
        if url not in files:
            raise httpx.ConnectError(f"no route to {url}")
        return httpx.Response(200, content=files[url], request=httpx.Request("GET", url))

    monkeypatch.setattr("tailcam.plugins.market.httpx.get", fake_get)
    ctx = client.app.state.ctx
    ctx.config.plugins.registry_url = "https://registry.test/index.json"
    return ctx


def test_market_api_flow(client, monkeypatch):
    _wire_fake_registry(client, monkeypatch)

    body = client.get("/api/plugins/market").json()
    assert body["market"][0]["id"] == "testplug"
    assert body["market"][0]["installed"] is False

    body = client.post("/api/plugins/market/install", json={"id": "testplug"}).json()
    assert body["market"][0]["installed"] is True
    inst = body["installed"][0]
    assert inst["id"] == "testplug" and inst["loaded"] is True  # hot-loaded

    # disable -> not loaded, still on disk
    body = client.post(
        "/api/plugins/installed/testplug/toggle", json={"enabled": False}
    ).json()
    inst = body["installed"][0]
    assert inst["enabled"] is False and inst["loaded"] is False

    # uninstall -> gone
    body = client.request("DELETE", "/api/plugins/installed/testplug").json()
    assert body["installed"] == []


def test_market_api_install_failure_maps_to_400(client, monkeypatch):
    ctx = _wire_fake_registry(client, monkeypatch)
    ctx.config.plugins.registry_url = "https://registry.test/index.json"
    resp = client.post("/api/plugins/market/install", json={"id": "ghost"})
    assert resp.status_code == 400
    assert "registry" in resp.json()["detail"]


def test_market_api_uninstall_missing_404(client, monkeypatch):
    _wire_fake_registry(client, monkeypatch)
    assert client.request("DELETE", "/api/plugins/installed/ghost").status_code == 404
