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


@pytest.mark.parametrize("port", [True, False, "9123", "broken", 0, -1, 65536, 9123.5])
@pytest.mark.parametrize("if_missing", [False, True])
def test_invalid_saved_port_rejected_before_any_write(isolated_env, port, if_missing):
    cfg = AppConfig()
    cfg.node.roles = []
    cfg.server.port = port
    cfg.save()
    original = paths.config_file().read_bytes()
    with pytest.raises(NodeConfigError, match="Port must be an integer"):
        configure(preset="storage", if_missing=if_missing)
    assert paths.config_file().read_bytes() == original


@pytest.mark.parametrize("port", [True, False, "9123", 9123.5])
def test_invalid_explicit_port_never_writes(isolated_env, port):
    with pytest.raises(NodeConfigError, match="Port must be an integer"):
        configure(port=port)
    assert not paths.config_file().exists()


def test_explicit_port_can_repair_saved_value(isolated_env):
    cfg = AppConfig()
    cfg.node.roles = []
    cfg.server.port = "broken"
    cfg.save()
    original = paths.config_file().read_bytes()
    with pytest.raises(NodeConfigError, match="Port must be an integer"):
        configure(port=9123, if_missing=True)
    assert paths.config_file().read_bytes() == original
    result = configure(port=9123)
    assert result["roles"] == []
    assert result["port"] == 9123
    assert AppConfig.load().server.port == 9123


@pytest.fixture
def legacy_setup(tmp_path, monkeypatch):
    from tailcam import migrate

    legacy_config = tmp_path / "AnyCam" / "config"
    legacy_data = tmp_path / "AnyCam" / "data"
    target_config = tmp_path / "TailCam" / "config"
    target_data = tmp_path / "TailCam" / "data"
    legacy_config.mkdir(parents=True)
    (legacy_data / "media").mkdir(parents=True)
    (legacy_config / "config.toml").write_text(
        '[node]\nroles = ["storage"]\nname = "Legacy storage"\n'
        '[server]\nport = 9123\n', encoding="utf-8",
    )
    (legacy_data / "anycam.db").write_bytes(b"legacy database fixture")
    (legacy_data / "media" / "keep.mp4").write_bytes(b"legacy media fixture")
    # Every source and destination is a fixture path. Do not discover or move
    # the developer's legacy install, even if one exists on the test machine.
    monkeypatch.setattr(paths, "config_dir", lambda: target_config)
    monkeypatch.setattr(paths, "config_file", lambda: target_config / "config.toml")
    monkeypatch.setattr(paths, "data_dir", lambda: target_data)
    monkeypatch.setattr(paths, "legacy_config_dir", lambda: legacy_config)
    monkeypatch.setattr(paths, "legacy_data_dir", lambda: legacy_data)
    monkeypatch.setattr(migrate, "_paths_overridden", lambda: False)
    return legacy_config, legacy_data, target_config, target_data


def test_legacy_setup_dry_run_previews_without_moving_any_files(legacy_setup):
    legacy_config, legacy_data, target_config, target_data = legacy_setup
    original_config = (legacy_config / "config.toml").read_bytes()
    result = CliRunner().invoke(app, ["setup", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["roles"] == ["storage"]
    assert summary["name"] == "Legacy storage"
    assert summary["port"] == 9123
    assert summary["changed"] is False
    assert (legacy_config / "config.toml").read_bytes() == original_config
    assert (legacy_data / "anycam.db").read_bytes() == b"legacy database fixture"
    assert (legacy_data / "media" / "keep.mp4").read_bytes() == b"legacy media fixture"
    assert not target_config.exists()
    assert not target_data.exists()


def test_real_setup_still_migrates_valid_legacy_state(legacy_setup):
    legacy_config, legacy_data, target_config, target_data = legacy_setup
    result = CliRunner().invoke(app, ["setup", "--json"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["roles"] == ["storage"]
    assert summary["name"] == "Legacy storage"
    assert summary["port"] == 9123
    assert not (legacy_config / "config.toml").exists()
    assert not (legacy_data / "anycam.db").exists()
    assert (target_config / "config.toml").exists()
    assert (target_data / "tailcam.db").read_bytes() == b"legacy database fixture"
    assert (target_data / "media" / "keep.mp4").read_bytes() == b"legacy media fixture"


@pytest.mark.parametrize("dry_run", [False, True])
def test_invalid_legacy_config_fails_before_migration(legacy_setup, dry_run):
    legacy_config, legacy_data, target_config, target_data = legacy_setup
    invalid = b'[node]\nroles = []\n[server]\nport = "broken"\n'
    (legacy_config / "config.toml").write_bytes(invalid)
    args = ["setup", "--json"] + (["--dry-run"] if dry_run else [])
    result = CliRunner().invoke(app, args)
    assert result.exit_code != 0
    assert "Port must be an integer" in result.output
    assert (legacy_config / "config.toml").read_bytes() == invalid
    assert (legacy_data / "anycam.db").read_bytes() == b"legacy database fixture"
    assert not target_config.exists()
    assert not target_data.exists()


def test_invalid_setup_options_do_not_migrate_legacy_state(legacy_setup):
    legacy_config, legacy_data, target_config, target_data = legacy_setup
    result = CliRunner().invoke(app, ["setup", "--preset", "typo"])
    assert result.exit_code != 0
    assert (legacy_config / "config.toml").exists()
    assert (legacy_data / "anycam.db").exists()
    assert not target_config.exists()
    assert not target_data.exists()


def test_preview_respects_explicit_paths_and_existing_target(legacy_setup, monkeypatch):
    from tailcam import migrate

    _, _, target_config, target_data = legacy_setup
    monkeypatch.setattr(migrate, "_paths_overridden", lambda: True)
    assert configure(dry_run=True)["roles"] == ["capture", "storage", "analysis", "training"]
    assert not target_config.exists()
    assert not target_data.exists()
    monkeypatch.setattr(migrate, "_paths_overridden", lambda: False)
    target_config.mkdir(parents=True)
    (target_config / "config.toml").write_text('[node]\nroles = []\n', encoding="utf-8")
    assert configure(dry_run=True)["roles"] == []


def test_media_only_migration_does_not_preview_blocked_legacy_config(legacy_setup):
    from tailcam import migrate

    legacy_config, legacy_data, target_config, target_data = legacy_setup
    target_config.mkdir(parents=True)
    recovery = target_config / "config.toml.bad"
    recovery.write_bytes(b"existing target recovery file")
    assert migrate.needs_migration() is True  # Media is still eligible.
    assert migrate.pending_config_file() is None

    preview = CliRunner().invoke(app, ["setup", "--dry-run", "--json"])
    assert preview.exit_code == 0, preview.output
    preview_summary = json.loads(preview.stdout)
    assert preview_summary["roles"] == ["capture", "storage", "analysis", "training"]
    assert preview_summary["port"] == 8088
    assert (legacy_config / "config.toml").exists()
    assert (legacy_data / "anycam.db").exists()
    assert not (target_config / "config.toml").exists()
    assert not target_data.exists()

    actual = CliRunner().invoke(app, ["setup", "--json"])
    assert actual.exit_code == 0, actual.output
    actual_summary = json.loads(actual.stdout)
    assert actual_summary["roles"] == preview_summary["roles"]
    assert actual_summary["port"] == preview_summary["port"]
    assert (legacy_config / "config.toml").exists()
    assert recovery.read_bytes() == b"existing target recovery file"
    assert (target_data / "tailcam.db").read_bytes() == b"legacy database fixture"


def test_hub_compose_has_no_hardware_passthrough():
    import yaml

    cfg = yaml.safe_load((Path(__file__).parents[1] / "docker-compose.hub.yml").read_text())
    service = cfg["services"]["tailcam"]
    assert service["environment"]["TAILCAM_PRESET"] == "hub"
    for key in ("devices", "device_cgroup_rules", "cap_add", "privileged"):
        assert key not in service
    assert all(not volume.startswith("/dev") for volume in service["volumes"])
