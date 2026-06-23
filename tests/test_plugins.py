from __future__ import annotations

from tailcam.ai.analyzer import OllamaAnalyzer
from tailcam.config import AIConfig, AppConfig
from tailcam.plugins.registry import PluginRegistry


def test_registry_discovers_builtins():
    reg = PluginRegistry(load_external=False)
    assert [p.id for p in reg.analyzer_providers()] == ["ollama"]
    assert {c.id for c in reg.notification_channels()} == {"discord", "telegram", "webhook"}
    ids = {i.id for i in reg.plugin_infos()}
    assert {"builtin-ollama", "builtin-channels"} <= ids
    kinds = {i.id: i.kind for i in reg.plugin_infos()}
    assert kinds["builtin-ollama"] == "ai"
    assert kinds["builtin-channels"] == "notification"


def test_ollama_provider_builds_analyzer():
    reg = PluginRegistry(load_external=False)
    provider = reg.analyzer_provider("ollama")
    assert provider is not None
    analyzer = provider.build(AIConfig())
    assert isinstance(analyzer, OllamaAnalyzer)


def test_unknown_provider_returns_none():
    reg = PluginRegistry(load_external=False)
    assert reg.analyzer_provider("nope") is None


_DROPIN = '''
from tailcam.plugins.hookspecs import hookimpl, PluginInfo


class TestChannel:
    id = "testchan"
    name = "Test channel"

    def configured(self, config):
        return True

    def send(self, event, config):
        pass


@hookimpl
def tailcam_notification_channels():
    return [TestChannel()]


@hookimpl
def tailcam_plugin_info():
    return [PluginInfo(id="dropin-test", name="Drop-in test", kind="notification")]
'''


def test_dropin_plugin_loaded(tmp_path):
    (tmp_path / "mychannel.py").write_text(_DROPIN)
    reg = PluginRegistry(load_external=True, dropin_dir=tmp_path)
    assert "testchan" in {c.id for c in reg.notification_channels()}
    assert "dropin-test" in {i.id for i in reg.plugin_infos()}
    assert reg.errors == []


def test_bad_dropin_is_recorded_not_raised(tmp_path):
    (tmp_path / "broken.py").write_text("this is not valid python !!!\n")
    reg = PluginRegistry(load_external=True, dropin_dir=tmp_path)
    # still works, error captured rather than crashing startup
    assert reg.analyzer_provider("ollama") is not None
    assert any("broken.py" in e for e in reg.errors)


def test_config_roundtrip():
    cfg = AppConfig()
    data = cfg.to_dict()
    assert "plugins" in data
    assert data["ai"]["provider"] == "ollama"
    restored = AppConfig.from_dict(data)
    assert restored.plugins.load_dropins is True
    assert restored.ai.provider == "ollama"


def test_rest_plugins_endpoint(client):
    resp = client.get("/api/plugins")
    assert resp.status_code == 200
    body = resp.json()
    ids = {p["id"] for p in body["plugins"]}
    assert {"builtin-ollama", "builtin-channels"} <= ids
    assert "ollama" in {p["id"] for p in body["analyzer_providers"]}
    assert {"discord", "telegram", "webhook"} <= {c["id"] for c in body["notification_channels"]}
    assert body["active_provider"] == "ollama"
