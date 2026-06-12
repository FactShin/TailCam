"""The AnyCam → TailCam rename: clean paths, explicit data migration, and
legacy service-unit cleanup.

These run on Linux CI; platform-specific branches are exercised by
monkeypatching ``sys.platform``.
"""

from __future__ import annotations

import sys

from tailcam import migrate, paths
from tailcam.service import installer


def _use_linux_dirs(monkeypatch, tmp_path):
    for var in ("TAILCAM_CONFIG_DIR", "TAILCAM_CONFIG", "TAILCAM_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


# --- clean paths (no implicit legacy fallback) ------------------------------


def test_paths_resolve_to_tailcam(monkeypatch, tmp_path):
    _use_linux_dirs(monkeypatch, tmp_path)
    assert paths.config_dir() == tmp_path / "config" / "tailcam"
    assert paths.data_dir() == tmp_path / "data" / "tailcam"
    assert paths.database_file() == tmp_path / "data" / "tailcam" / "tailcam.db"


def test_paths_ignore_populated_legacy_dir(monkeypatch, tmp_path):
    # Even if an AnyCam dir is full of data, the path functions point at TailCam;
    # bringing the data across is the migration's job, not a silent fallback.
    _use_linux_dirs(monkeypatch, tmp_path)
    legacy = tmp_path / "data" / "anycam"
    legacy.mkdir(parents=True)
    (legacy / "anycam.db").write_text("x")
    assert paths.data_dir() == tmp_path / "data" / "tailcam"


def test_tailcam_config_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("TAILCAM_DATA_DIR", str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom"


# --- migration --------------------------------------------------------------


def test_needs_migration_detects_populated_legacy(monkeypatch, tmp_path):
    _use_linux_dirs(monkeypatch, tmp_path)
    legacy = tmp_path / "config" / "anycam"
    legacy.mkdir(parents=True)
    (legacy / "config.toml").write_text("")
    assert migrate.needs_migration() is True


def test_needs_migration_false_when_nothing_legacy(monkeypatch, tmp_path):
    _use_linux_dirs(monkeypatch, tmp_path)
    assert migrate.needs_migration() is False


def test_migrate_moves_config_media_and_renames_db(monkeypatch, tmp_path):
    _use_linux_dirs(monkeypatch, tmp_path)
    cfg_legacy = tmp_path / "config" / "anycam"
    data_legacy = tmp_path / "data" / "anycam"
    cfg_legacy.mkdir(parents=True)
    data_legacy.mkdir(parents=True)
    (cfg_legacy / "config.toml").write_text("[server]\nport = 9000\n")
    (data_legacy / "anycam.db").write_text("DB")
    (data_legacy / "anycam.db-wal").write_text("WAL")
    media = data_legacy / "media"
    media.mkdir()
    (media / "clip.mp4").write_text("video")

    actions = migrate.migrate()

    assert (paths.config_dir() / "config.toml").read_text() == "[server]\nport = 9000\n"
    assert paths.database_file().read_text() == "DB"
    assert (paths.data_dir() / "tailcam.db-wal").read_text() == "WAL"
    assert (paths.media_dir() / "clip.mp4").read_text() == "video"
    assert not migrate.needs_migration()
    assert actions  # produced a log


def test_migrate_skips_legacy_venv(monkeypatch, tmp_path):
    # The old Linux data dir held the venv; it must not be carried across.
    _use_linux_dirs(monkeypatch, tmp_path)
    data_legacy = tmp_path / "data" / "anycam"
    (data_legacy / "venv" / "bin").mkdir(parents=True)
    (data_legacy / "anycam.db").write_text("DB")

    migrate.migrate()

    assert paths.database_file().exists()
    assert not (paths.data_dir() / "venv").exists()


def test_migrate_noop_when_tailcam_already_has_data(monkeypatch, tmp_path):
    _use_linux_dirs(monkeypatch, tmp_path)
    data_legacy = tmp_path / "data" / "anycam"
    data_legacy.mkdir(parents=True)
    (data_legacy / "anycam.db").write_text("OLD")
    new = paths.data_dir()
    new.mkdir(parents=True)
    (new / "tailcam.db").write_text("CURRENT")

    migrate.migrate()

    # The live TailCam db is untouched; the legacy one is left where it was.
    assert paths.database_file().read_text() == "CURRENT"
    assert (data_legacy / "anycam.db").read_text() == "OLD"


# --- service-unit migration -------------------------------------------------


def test_systemd_install_removes_legacy_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    legacy_unit = unit_dir / "anycam.service"
    legacy_unit.write_text("[Unit]\n")
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))

    calls: list[list[str]] = []
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or type("P", (), {"returncode": 0})(),
    )

    installer.install()
    assert not legacy_unit.exists()
    assert (unit_dir / "tailcam.service").exists()
    assert ["systemctl", "--user", "disable", "--now", "anycam.service"] in calls
    assert ["systemctl", "--user", "restart", "tailcam.service"] in calls


def test_control_falls_back_to_legacy_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "anycam.service").write_text("[Unit]\n")
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))

    calls: list[list[str]] = []
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or type("P", (), {"returncode": 0})(),
    )

    assert "Restarted" in installer.restart()
    assert ["systemctl", "--user", "restart", "anycam.service"] in calls


# --- CLI --version ----------------------------------------------------------


def test_version_flag(monkeypatch):
    from typer.testing import CliRunner

    from tailcam import __version__
    from tailcam.cli import app

    # No legacy data around → the callback's migration check is a quick no-op.
    monkeypatch.setenv("TAILCAM_DATA_DIR", "/nonexistent/tailcam-test")
    monkeypatch.setenv("TAILCAM_CONFIG_DIR", "/nonexistent/tailcam-test-cfg")
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
