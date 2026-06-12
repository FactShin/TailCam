import httpx
from typer.testing import CliRunner

from anycam import update as upd
from anycam.cli import app

runner = CliRunner()


def test_parse_version_ordering():
    assert upd.parse_version("0.2.4") == (0, 2, 4)
    assert upd.parse_version("0.10.0") > upd.parse_version("0.9.9")
    assert upd.parse_version("1.0.0") > upd.parse_version("0.99.99")
    assert upd.parse_version("garbage") == (0,)


def test_latest_version_parses_remote(monkeypatch):
    body = '"""AnyCam."""\n\n__version__ = "9.9.9"\n'

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


def test_cli_update_installs_and_restarts(monkeypatch, isolated_env):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: "99.0.0")
    actions: list[str] = []
    monkeypatch.setattr(upd, "run_pip_upgrade", lambda: (actions.append("pip"), True)[1])

    from anycam.service import installer

    monkeypatch.setattr(installer, "restart", lambda: (actions.append("restart"), "Restarted")[1])
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert actions == ["pip", "restart"]
    assert "Updated to 99.0.0" in result.stdout


def test_cli_update_unreachable(monkeypatch, isolated_env):
    monkeypatch.setattr(upd, "latest_version", lambda **kw: None)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
