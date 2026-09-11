"""Installation config must never start workloads or discard existing state."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tailcam import paths
from tailcam.cli import app
from tailcam.config import AppConfig
from tailcam.node import NodeConfigError
from tailcam.setup import configure


def test_fresh_hub_first_write_and_no_workers(isolated_env, monkeypatch):
    from tailcam.ai.detector import BuiltinDetector
    from tailcam.camera.manager import CameraManager

    def forbidden(*args, **kwargs):
        pytest.fail("Setup must not touch cameras or models")

    monkeypatch.setattr(CameraManager, "discover", forbidden)
    monkeypatch.setattr(BuiltinDetector, "ensure_ready", forbidden)
    seen = []
    save = AppConfig.save

    def capture_save(cfg, *args, **kwargs):
        seen.append(cfg.node.roles)
        return save(cfg, *args, **kwargs)

    monkeypatch.setattr(AppConfig, "save", capture_save)
    result = configure(preset="hub", node_name="Workshop")
    assert result["roles"] == []
    assert seen == [[]]
    assert AppConfig.load().node.name == "Workshop"


def test_rerun_preserves_config_media_identity(store):
    cfg = AppConfig()
    cfg.node.roles = ["storage"]
    cfg.server.port = 9123
    cfg.storage.media_dir = "/custom/media"
    cfg.save()
    identity = store.get_node_id()
    media = paths.data_dir() / "keep.mp4"
    media.write_bytes(b"valuable media")
    original = paths.config_file().read_bytes()
    assert configure()["changed"] is False
    assert configure(preset="hub", if_missing=True)["roles"] == ["storage"]
    assert paths.config_file().read_bytes() == original
    assert media.read_bytes() == b"valuable media"
    assert store.get_node_id() == identity


@pytest.mark.parametrize(
    "options",
    [
        {"preset": "typo"},
        {"preset": "hub", "roles": "capture"},
        {"roles": "capture,capture"},
        {"roles": "unknown"},
        {"port": 0},
        {"port": 65536},
        {"node_name": "unsafe\nname"},
    ],
)
def test_invalid_choices_never_write(isolated_env, options):
    with pytest.raises(NodeConfigError):
        configure(**options)
    assert not paths.config_file().exists()


def test_corrupt_existing_config_is_preserved(isolated_env):
    paths.config_file().write_text('[node]\nroles = "hub"\n', encoding="utf-8")
    original = paths.config_file().read_bytes()
    with pytest.raises(NodeConfigError):
        configure(preset="hub", if_missing=True)
    assert paths.config_file().read_bytes() == original


def test_dry_run_and_cli_summary(isolated_env):
    result = CliRunner().invoke(
        app, ["setup", "--preset", "hub", "--port", "9123", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["url"] == "http://127.0.0.1:9123/"
    assert not paths.config_file().exists()
    result = CliRunner().invoke(app, ["setup", "--roles", "capture", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["roles"] == ["capture"]
    result = CliRunner().invoke(app, ["setup", "--quiet"])
    assert result.exit_code == 0 and result.output == ""


def test_hub_compose_has_no_hardware_passthrough():
    import yaml

    cfg = yaml.safe_load((Path(__file__).parents[1] / "docker-compose.hub.yml").read_text())
    service = cfg["services"]["tailcam"]
    assert service["environment"]["TAILCAM_PRESET"] == "hub"
    for key in ("devices", "device_cgroup_rules", "cap_add", "privileged"):
        assert key not in service
    assert all(not volume.startswith("/dev") for volume in service["volumes"])
