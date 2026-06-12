from typer.testing import CliRunner

from tailcam.cli import app
from tailcam.config import AppConfig

runner = CliRunner()


def test_config_sets_and_persists_port(isolated_env):
    result = runner.invoke(app, ["config", "--port", "9123", "--serve-port", "10000"])
    assert result.exit_code == 0
    cfg = AppConfig.load()
    assert cfg.server.port == 9123
    assert cfg.tailscale.serve_port == 10000


def test_status_runs(isolated_env):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "TailCam" in result.stdout


def test_doctor_runs(isolated_env):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Python 3.10+" in result.stdout


def test_cameras_lists_synthetic(isolated_env):
    result = runner.invoke(app, ["cameras"])
    assert result.exit_code == 0
    assert "synthetic" in result.stdout


def test_version(isolated_env):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0


def test_malformed_config_falls_back_to_defaults(isolated_env, tmp_path):
    from tailcam import paths
    from tailcam.config import AppConfig

    cfg = paths.config_file()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[ai]\nbase_url = "http://oops\n')  # unterminated string

    loaded = AppConfig.load()  # must NOT raise
    assert loaded.server.port == 8088  # defaults
    assert cfg.with_suffix(".toml.bad").exists()  # bad file preserved
    assert not cfg.exists()
