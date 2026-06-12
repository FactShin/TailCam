"""The AnyCam → TailCam rename must not strand pre-rename installs.

Covers: the `anycam` module shim, legacy config/data directory fallback, and
legacy service-unit cleanup on install.
"""

from __future__ import annotations

import sys

import tailcam
from tailcam import paths
from tailcam.service import installer


def test_anycam_shim_forwards_to_tailcam():
    import anycam
    import anycam.cli

    assert anycam.__version__ == tailcam.__version__
    from tailcam.cli import main

    assert anycam.cli.main is main


def _clear_path_env(monkeypatch):
    for var in (
        "TAILCAM_CONFIG_DIR",
        "TAILCAM_CONFIG",
        "TAILCAM_DATA_DIR",
        "ANYCAM_CONFIG_DIR",
        "ANYCAM_CONFIG",
        "ANYCAM_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


def test_config_dir_falls_back_to_populated_legacy(monkeypatch, tmp_path):
    _clear_path_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = tmp_path / "anycam"
    legacy.mkdir()
    (legacy / "config.toml").write_text("")
    assert paths.config_dir() == legacy


def test_config_dir_prefers_populated_tailcam(monkeypatch, tmp_path):
    _clear_path_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name in ("anycam", "tailcam"):
        d = tmp_path / name
        d.mkdir()
        (d / "config.toml").write_text("")
    assert paths.config_dir() == tmp_path / "tailcam"


def test_data_dir_ignores_bare_tailcam_dir(monkeypatch, tmp_path):
    # The installer creates ~/.local/share/tailcam/ for the venv; a bare dir
    # without db/media must NOT steal the data dir from a legacy install.
    _clear_path_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    (tmp_path / "tailcam" / "venv").mkdir(parents=True)
    legacy = tmp_path / "anycam"
    legacy.mkdir()
    (legacy / "anycam.db").write_text("")
    assert paths.data_dir() == legacy
    assert paths.database_file() == legacy / "anycam.db"


def test_data_dir_defaults_to_tailcam_when_fresh(monkeypatch, tmp_path):
    _clear_path_env(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / "tailcam"
    assert paths.database_file().name == "tailcam.db"


def test_legacy_env_overrides_still_honored(monkeypatch, tmp_path):
    _clear_path_env(monkeypatch)
    monkeypatch.setenv("ANYCAM_DATA_DIR", str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom"


def test_systemd_install_removes_legacy_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    legacy_unit = unit_dir / "anycam.service"
    legacy_unit.write_text("[Unit]\n")
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))

    calls: list[list[str]] = []
    monkeypatch.setattr(
        installer.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type(
            "P", (), {"returncode": 0}
        )()
    )

    installer.install()
    assert not legacy_unit.exists()
    assert (unit_dir / "tailcam.service").exists()
    assert ["systemctl", "--user", "disable", "--now", "anycam.service"] in calls
    assert ["systemctl", "--user", "restart", "tailcam.service"] in calls


def test_control_falls_back_to_legacy_unit(monkeypatch, tmp_path):
    # A node updated in place still has only anycam.service; start/stop/restart
    # must control that unit instead of reporting "not installed".
    monkeypatch.setattr(sys, "platform", "linux")
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "anycam.service").write_text("[Unit]\n")
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))

    calls: list[list[str]] = []
    monkeypatch.setattr(
        installer.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or type(
            "P", (), {"returncode": 0}
        )()
    )

    assert "Restarted" in installer.restart()
    assert ["systemctl", "--user", "restart", "anycam.service"] in calls
