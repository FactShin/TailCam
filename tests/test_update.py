import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from tailcam import update as upd
from tailcam.cli import app

runner = CliRunner()


def test_parse_version_ordering():
    assert upd.parse_version("0.2.4") == (0, 2, 4)
    assert upd.parse_version("0.10.0") > upd.parse_version("0.9.9")
    assert upd.parse_version("1.0.0") > upd.parse_version("0.99.99")
    assert upd.parse_version("garbage") == (0,)


def test_parse_version_four_part_hotfix():
    # A 4-part hotfix release must sort AFTER its 3-part base; truncating to
    # three components made 1.8.1.1 == 1.8.1 and the update invisible.
    assert upd.parse_version("1.8.1.1") == (1, 8, 1, 1)
    assert upd.parse_version("1.8.1.1") > upd.parse_version("1.8.1")
    assert upd.parse_version("1.8.2") > upd.parse_version("1.8.1.1")
    assert upd.parse_version("v1.8.1.1-rc1") == (1, 8, 1, 1)


def test_run_pip_upgrade_disables_cache(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert upd.run_pip_upgrade() is True
    assert "--no-cache-dir" in seen[0]
    assert seen[0][-1] == upd.ZIP_URL


def test_installed_version_reads_fresh_interpreter(monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd[0] == sys.executable and "-c" in cmd
        return SimpleNamespace(returncode=0, stdout="9.9.9\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert upd.installed_version() == "9.9.9"
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="x")
    )
    assert upd.installed_version() is None


def test_latest_version_parses_remote(monkeypatch):
    body = '"""TailCam."""\n\n__version__ = "9.9.9"\n'

    def fake_get(url, **kw):
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    assert upd.latest_version() == "9.9.9"


def test_latest_version_unreachable(monkeypatch):
    def fake_get(url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert upd.latest_version() is None


def test_update_available_logic(monkeypatch):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: "99.0.0")
    current, latest, newer = upd.update_available(use_cache=False)
    assert latest == "99.0.0" and newer is True

    monkeypatch.setattr(upd, "latest_version", lambda **kw: "0.0.1")
    _, _, newer = upd.update_available(use_cache=False)
    assert newer is False


def test_cli_update_check_only(monkeypatch, isolated_env):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: "99.0.0")
    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "Update available" in result.stdout
    assert "99.0.0" in result.stdout


def test_cli_update_up_to_date(monkeypatch, isolated_env):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: "0.0.1")
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "Up to date" in result.stdout


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows updates hand off to the installer"
)
def test_cli_update_installs_and_restarts(monkeypatch, isolated_env):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: "99.0.0")
    actions: list[str] = []
    monkeypatch.setattr(upd, "run_pip_upgrade", lambda: (actions.append("pip"), True)[1])

    from tailcam.service import installer

    monkeypatch.setattr(installer, "is_installed", lambda: False)
    monkeypatch.setattr(installer, "restart", lambda: (actions.append("restart"), "Restarted")[1])
    monkeypatch.setattr(upd, "installed_version", lambda: "99.0.0")
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert actions == ["pip", "restart"]
    assert "Updated to 99.0.0" in result.stdout


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows updates hand off to the installer"
)
def test_cli_update_reports_version_mismatch(monkeypatch, isolated_env):
    # pip "succeeded" but the on-disk version didn't change (cached wheel,
    # wrong interpreter…): never claim "Updated to X".
    monkeypatch.setattr(upd, "latest_version", lambda **kw: "99.0.0")
    monkeypatch.setattr(upd, "run_pip_upgrade", lambda: True)

    from tailcam.service import installer

    monkeypatch.setattr(installer, "is_installed", lambda: False)
    monkeypatch.setattr(installer, "restart", lambda: "Restarted")
    monkeypatch.setattr(upd, "installed_version", lambda: "1.0.0")
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "Updated to" not in result.stdout
    assert "1.0.0" in result.stdout and "99.0.0" in result.stdout


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows updates hand off to the installer"
)
def test_cli_update_reinstalls_unit_when_service_installed(monkeypatch, isolated_env):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: "99.0.0")
    monkeypatch.setattr(upd, "run_pip_upgrade", lambda: True)
    monkeypatch.setattr(upd, "installed_version", lambda: "99.0.0")

    from tailcam.service import installer

    actions: list[str] = []
    monkeypatch.setattr(installer, "is_installed", lambda: True)
    monkeypatch.setattr(installer, "install", lambda: (actions.append("install"), "Installed")[1])
    monkeypatch.setattr(installer, "restart", lambda: (actions.append("restart"), "Restarted")[1])
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert actions == ["install"]


def test_desktop_apply_update_mirrors_cli(monkeypatch):
    # The tray's updater must re-render the unit (install) when a service is
    # registered, exactly like `tailcam update`, and only restart otherwise.
    from tailcam.desktop import updates
    from tailcam.service import installer

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(upd, "run_pip_upgrade", lambda: True)
    monkeypatch.setattr(installer, "install", lambda: "Installed")
    monkeypatch.setattr(installer, "restart", lambda: "Restarted")
    monkeypatch.setattr(installer, "is_installed", lambda: True)
    assert updates.apply_update() == "Installed"
    monkeypatch.setattr(installer, "is_installed", lambda: False)
    assert updates.apply_update() == "Restarted"
    monkeypatch.setattr(upd, "run_pip_upgrade", lambda: False)
    assert "failed" in updates.apply_update()


def test_cli_update_unreachable(monkeypatch, isolated_env):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: None)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
