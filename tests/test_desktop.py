"""Desktop app (issue #38, macOS milestone): pure core + generated artifacts.

Everything here runs headless on Linux — the GUI adapters keep their imports
lazy, and the macOS bundle is three generated files verifiable byte-by-byte.
"""

from __future__ import annotations

import plistlib
import struct

import httpx

from tailcam.desktop import menu as menu_model
from tailcam.desktop.menu import build_menu
from tailcam.desktop.nodes import _peer_dashboard_url, fetch_nodes
from tailcam.desktop.state import MenuSpec, Node, ServerState


def _labels(specs: list[MenuSpec]) -> list[str]:
    return [s.label for s in specs if not s.separator]


def _actions(specs: list[MenuSpec]) -> set[str]:
    out = set()
    for s in specs:
        if s.action:
            out.add(s.action)
        for c in s.children:
            if c.action:
                out.add(c.action)
    return out


# ------------------------------------------------------------------ menu model
def test_menu_running_local():
    specs = build_menu(ServerState(installed=True, running=True, version="1.3.0"), [])
    acts = _actions(specs)
    assert menu_model.OPEN_DASHBOARD in acts
    assert menu_model.SERVICE_RESTART in acts and menu_model.SERVICE_STOP in acts
    assert menu_model.SERVICE_START not in acts
    assert menu_model.QUIT in acts
    assert any("running" in label for label in _labels(specs))


def test_menu_stopped_local():
    specs = build_menu(ServerState(installed=True, running=False), [])
    acts = _actions(specs)
    assert menu_model.SERVICE_START in acts
    assert menu_model.SERVICE_STOP not in acts
    # Dashboard entry present but disabled while the server is down.
    dash = next(s for s in specs if s.action == menu_model.OPEN_DASHBOARD)
    assert dash.enabled is False


def test_menu_not_installed_offers_install():
    specs = build_menu(ServerState(installed=False, running=False), [])
    assert menu_model.SERVICE_INSTALL in _actions(specs)
    assert any("not installed" in label for label in _labels(specs))


def test_menu_update_available_badge():
    state = ServerState(installed=True, running=True, update_available=True, update_latest="9.9.9")
    specs = build_menu(state, [])
    badge = next(s for s in specs if s.action == menu_model.APPLY_UPDATE)
    assert "9.9.9" in badge.label and badge.enabled


def test_menu_client_mode_hides_service_controls():
    state = ServerState(running=True, client_mode=True, base_url="https://mac.ts.net:8443/")
    specs = build_menu(state, [])
    acts = _actions(specs)
    assert not acts & {
        menu_model.SERVICE_START, menu_model.SERVICE_STOP,
        menu_model.SERVICE_RESTART, menu_model.SERVICE_INSTALL,
    }
    assert any("mac.ts.net" in label for label in _labels(specs))
    # Remote update is not offered from the shell.
    state.update_available = True
    badge = next(s for s in build_menu(state, []) if s.action == menu_model.APPLY_UPDATE)
    assert badge.enabled is False


def test_menu_nodes_submenu_states():
    nodes = [
        Node(key="local", host="mac-mini", kind="local", url="http://localhost:8088/"),
        Node(key="p1", host="ubuntu.tail.ts.net", kind="peer", camera_count=2,
             url="https://ubuntu.tail.ts.net:8443/"),
        Node(key="p2", host="bare-host", kind="peer", url=None),
        Node(key="p3", host="down.tail.ts.net", kind="peer", online=False,
             url="https://down.tail.ts.net:8443/"),
    ]
    specs = build_menu(ServerState(installed=True, running=True), nodes)
    sub = next(s for s in specs if s.label == "Nodes")
    by_label = {c.label: c for c in sub.children}
    assert any(c.action.startswith(menu_model.OPEN_NODE_PREFIX) for c in sub.children)
    assert by_label["ubuntu.tail.ts.net · 2 cams"].enabled
    serve_hint = next(c for c in sub.children if "Serve" in c.label)
    assert not serve_hint.enabled  # peer without a routable HTTPS dashboard
    offline = next(c for c in sub.children if "offline" in c.label)
    assert not offline.enabled
    # The local node never appears in its own submenu.
    assert not any("mac-mini" in c.label for c in sub.children)


# ------------------------------------------------------------------ nodes
def test_peer_dashboard_url_rules():
    assert _peer_dashboard_url("ubuntu.tail02.ts.net") == "https://ubuntu.tail02.ts.net:8443/"
    assert _peer_dashboard_url("ubuntu.tail02.ts.net.") == "https://ubuntu.tail02.ts.net:8443/"
    assert _peer_dashboard_url("bare-hostname") is None
    assert _peer_dashboard_url("") is None


def test_fetch_nodes_parses_hosts(monkeypatch):
    hosts = [
        {"host": "mac-mini.ts.net", "node_key": "local", "kind": "local",
         "online": True, "camera_count": 2, "version": "1.3.0"},
        {"host": "ubuntu.tail.ts.net", "node_key": "abc", "kind": "peer",
         "online": True, "camera_count": 1, "version": "1.2.2"},
    ]

    def fake_get(url, timeout):
        assert url.endswith("/api/hosts")
        return httpx.Response(200, json=hosts, request=httpx.Request("GET", url))

    monkeypatch.setattr("tailcam.desktop.nodes.httpx.get", fake_get)
    nodes = fetch_nodes("http://localhost:8088/")
    assert nodes[0].kind == "local" and nodes[0].url == "http://localhost:8088/"
    assert nodes[1].url == "https://ubuntu.tail.ts.net:8443/"


def test_fetch_nodes_empty_on_error(monkeypatch):
    def boom(url, timeout):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("tailcam.desktop.nodes.httpx.get", boom)
    assert fetch_nodes("http://localhost:8088/") == []


# ------------------------------------------------------------------ NodeClient
def test_nodeclient_running_from_probe_not_installer(monkeypatch, isolated_env):
    from tailcam.desktop.server import NodeClient

    # Windows regression: is_installed() is hardcoded True there — "running"
    # must come exclusively from the API probe.
    monkeypatch.setattr("tailcam.desktop.server.installer.is_installed", lambda: True)

    def down(url, timeout):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr("tailcam.desktop.server.httpx.get", down)
    state = NodeClient().state()
    assert state.installed is True
    assert state.running is False


def test_nodeclient_state_running(monkeypatch, isolated_env):
    from tailcam.desktop.server import NodeClient

    monkeypatch.setattr("tailcam.desktop.server.installer.is_installed", lambda: True)

    def fake_get(url, timeout):
        req = httpx.Request("GET", url)
        if url.endswith("/api/system") or url.endswith("api/system"):
            return httpx.Response(200, json={"version": "1.3.0"}, request=req)
        payload = {"current": "1.3.0", "latest": "1.4.0", "available": True}
        return httpx.Response(200, json=payload, request=req)

    monkeypatch.setattr("tailcam.desktop.server.httpx.get", fake_get)
    state = NodeClient().state()
    assert state.running and state.version == "1.3.0"
    assert state.update_available and state.update_latest == "1.4.0"


def test_nodeclient_client_mode(monkeypatch):
    from tailcam.desktop.server import NodeClient

    client = NodeClient("https://mac.ts.net:8443")
    assert client.base_url == "https://mac.ts.net:8443/"
    assert client.client_mode is True

    def down(url, timeout):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr("tailcam.desktop.server.httpx.get", down)
    state = client.state()
    assert state.client_mode is True
    assert state.installed is False  # never consults the local installer


# ------------------------------------------------------------------ macOS bundle
def test_macos_bundle_artifacts(tmp_path):
    from tailcam.desktop import macos_bundle as mb

    bundle = mb.install_app(applications=tmp_path, python="/fake/venv/bin/python")
    assert bundle == tmp_path / "TailCam.app"

    with (bundle / "Contents" / "Info.plist").open("rb") as fh:
        plist = plistlib.load(fh)
    assert plist["CFBundleExecutable"] == "TailCam"
    assert plist["CFBundleIdentifier"] == "com.tailcam.app"
    assert plist["CFBundlePackageType"] == "APPL"
    assert plist["LSUIElement"] is True
    # The shell never opens cameras — reinstalls must not disturb TCC grants.
    assert "NSCameraUsageDescription" not in plist

    stub = bundle / "Contents" / "MacOS" / "TailCam"
    text = stub.read_text()
    assert text.startswith("#!/bin/zsh")
    assert '"/fake/venv/bin/python" -m tailcam app' in text
    assert stub.stat().st_mode & 0o755 == 0o755

    icns = (bundle / "Contents" / "Resources" / "TailCam.icns").read_bytes()
    assert icns[:4] == b"icns"
    total = struct.unpack(">I", icns[4:8])[0]
    assert total == len(icns)
    # Walk the chunks: every payload must be a valid PNG, one must be 512px.
    offset, seen = 8, []
    while offset < len(icns):
        ctype = icns[offset:offset + 4]
        clen = struct.unpack(">I", icns[offset + 4:offset + 8])[0]
        payload = icns[offset + 8:offset + clen]
        assert payload[:8] == b"\x89PNG\r\n\x1a\n", ctype
        seen.append(ctype)
        offset += clen
    assert b"ic09" in seen  # the 512px entry


def test_macos_bundle_idempotent(tmp_path):
    from tailcam.desktop import macos_bundle as mb

    first = mb.install_app(applications=tmp_path, python="/v/bin/python")
    again = mb.install_app(applications=tmp_path, python="/v2/bin/python")
    assert first == again
    # Re-install re-bakes the venv path (the upgrade contract).
    assert '"/v2/bin/python"' in (again / "Contents" / "MacOS" / "TailCam").read_text()


def test_macos_bundle_codesign_argv(tmp_path, monkeypatch):
    from tailcam.desktop import macos_bundle as mb

    calls = []
    monkeypatch.setattr(mb.shutil, "which", lambda name: "/usr/bin/codesign")
    monkeypatch.setattr(
        mb, "run_hidden",
        lambda cmd, **kw: calls.append(cmd) or type("P", (), {"returncode": 0, "stderr": ""})(),
    )
    mb.install_app(applications=tmp_path, python="/v/bin/python")
    assert calls, "codesign was not invoked"
    assert calls[0][:5] == ["codesign", "--force", "--deep", "-s", "-"]


def test_macos_bundle_uninstall(tmp_path):
    from tailcam.desktop import macos_bundle as mb

    mb.install_app(applications=tmp_path, python="/v/bin/python")
    assert mb.uninstall_app(applications=tmp_path) is True
    assert not (tmp_path / "TailCam.app").exists()
    assert mb.uninstall_app(applications=tmp_path) is False


# ------------------------------------------------------------------ CLI
def test_cli_app_smoke_headless(isolated_env, monkeypatch):
    from typer.testing import CliRunner

    from tailcam.cli import app

    def down(url, timeout):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr("tailcam.desktop.server.httpx.get", down)
    result = CliRunner().invoke(app, ["app", "--smoke"])
    assert result.exit_code == 0, result.output
    assert "menu_items" in result.output


def test_cli_app_install_on_macos(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from tailcam import cli
    from tailcam.desktop import macos_bundle as mb

    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(mb.Path, "home", classmethod(lambda cls: tmp_path))
    result = CliRunner().invoke(cli.app, ["app", "install"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "Applications" / "TailCam.app" / "Contents" / "Info.plist").exists()

    result = CliRunner().invoke(cli.app, ["app", "uninstall"])
    assert result.exit_code == 0
    assert not (tmp_path / "Applications" / "TailCam.app").exists()


# ------------------------------------------------------------------ single instance
def test_single_instance_lock(isolated_env):
    from tailcam.desktop.app import SingleInstance

    first = SingleInstance()
    assert first.acquire() is True
    opened = []
    first.serve_open_signals(lambda: opened.append(True))

    second = SingleInstance()
    assert second.acquire() is False  # signals the first instead

    import time

    deadline = time.time() + 3.0
    while not opened and time.time() < deadline:
        time.sleep(0.05)
    assert opened, "second launch did not signal the first to open the dashboard"
    first.release()

    # Stale lock (owner gone): the next launch acquires normally.
    third = SingleInstance()
    assert third.acquire() is True
    third.release()


def test_smoke_model_shape(isolated_env, monkeypatch):
    from tailcam.desktop.app import DesktopApp

    def down(url, timeout):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr("tailcam.desktop.server.httpx.get", down)
    result = DesktopApp().smoke()
    assert result["running"] is False
    assert result["menu_items"] >= 4
    assert "quit" in result["actions"]


# ------------------------------------------------------------------ Linux desktop
def test_linux_desktop_entry_golden(tmp_path):
    from tailcam.desktop import linux_desktop as ld

    entry = ld.install_entries(home=tmp_path, python="/venv/bin/python")
    assert entry == tmp_path / ".local/share/applications/tailcam.desktop"
    text = entry.read_text()
    icon = tmp_path / ".local/share/icons/hicolor/512x512/apps/tailcam.png"
    # Golden content: every load-bearing freedesktop key.
    assert "[Desktop Entry]" in text and "Type=Application" in text
    assert "Name=TailCam" in text
    assert f"Icon={icon}" in text
    assert "Terminal=false" in text
    assert "Categories=AudioVideo;Video;Network;" in text
    # Exec uses an ABSOLUTE venv command (console script or python -m).
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert "/venv/bin/" in exec_line and exec_line.endswith(" app")
    # Icon was actually copied and is a PNG.
    assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # No autostart unless asked.
    assert not (tmp_path / ".config/autostart/tailcam-tray.desktop").exists()


def test_linux_desktop_autostart_and_uninstall(tmp_path):
    from tailcam.desktop import linux_desktop as ld

    ld.install_entries(home=tmp_path, python="/venv/bin/python", autostart=True)
    auto = tmp_path / ".config/autostart/tailcam-tray.desktop"
    text = auto.read_text()
    assert "--no-window" in text  # login start is tray-only
    assert "X-GNOME-Autostart-enabled=true" in text

    assert ld.uninstall_entries(home=tmp_path) is True
    assert not auto.exists()
    assert not (tmp_path / ".local/share/applications/tailcam.desktop").exists()
    assert not (tmp_path / ".local/share/icons/hicolor/512x512/apps/tailcam.png").exists()
    assert ld.uninstall_entries(home=tmp_path) is False


def test_linux_exec_prefers_console_script(tmp_path):
    from tailcam.desktop.linux_desktop import _tailcam_exec

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "tailcam").write_text("#!/bin/sh\n")
    py = fake_bin / "python"
    py.write_text("")
    assert _tailcam_exec(str(py)) == f"{fake_bin}/tailcam app"
    # No console script -> python -m fallback.
    (fake_bin / "tailcam").unlink()
    assert _tailcam_exec(str(py)) == f"{fake_bin}/python -m tailcam app"


def test_linux_reinstall_rebakes_venv_path(tmp_path):
    from tailcam.desktop import linux_desktop as ld

    ld.install_entries(home=tmp_path, python="/old-venv/bin/python")
    ld.install_entries(home=tmp_path, python="/new-venv/bin/python")
    text = (tmp_path / ".local/share/applications/tailcam.desktop").read_text()
    assert "/new-venv/bin/" in text and "/old-venv/" not in text


def test_cli_app_install_on_linux(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from tailcam import cli
    from tailcam.desktop import linux_desktop as ld

    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(ld.Path, "home", classmethod(lambda cls: tmp_path))
    result = CliRunner().invoke(cli.app, ["app", "install", "--autostart"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".local/share/applications/tailcam.desktop").exists()
    assert (tmp_path / ".config/autostart/tailcam-tray.desktop").exists()

    result = CliRunner().invoke(cli.app, ["app", "uninstall"])
    assert result.exit_code == 0
    assert not (tmp_path / ".local/share/applications/tailcam.desktop").exists()


def test_linux_gui_diagnostics_shape():
    from tailcam.desktop.linux_desktop import gui_diagnostics

    rows = gui_diagnostics()
    assert rows, "diagnostics must always return at least the gi row"
    for ok_flag, label, hint in rows:
        assert isinstance(ok_flag, bool) and label
        if not ok_flag:
            assert hint  # every failure carries a remediation


# ------------------------------------------------------------------ Windows shortcut
def test_windows_shortcut_ps_golden(tmp_path, monkeypatch):
    # Force the pythonw path deterministically (no real Windows interpreter).
    from pathlib import Path as _P

    from tailcam.desktop import windows_shortcut as ws
    monkeypatch.setattr(
        ws, "_pythonw",
        lambda python=None: _P(r"C:\Users\me\AppData\Local\TailCam\venv\Scripts\pythonw.exe"),
    )
    monkeypatch.setattr(ws, "_icon_path", lambda home=None: __import__("pathlib").Path(
        r"C:\Users\me\AppData\Local\TailCam\tailcam.ico"
    ))
    captured = {}
    monkeypatch.setattr(ws, "_run_ps", lambda script: captured.setdefault("s", script))

    lnk = ws.install_shortcut(home=tmp_path, autostart=True)
    assert lnk == ws.shortcut_path(tmp_path)
    s = captured["s"]
    # Targets pythonw + -m tailcam app (no console window, no launcher stub).
    assert "WScript.Shell" in s and "CreateShortcut(" in s
    assert r"pythonw.exe" in s
    assert "$s.Arguments = '-m tailcam app'" in s
    assert "$s.Save()" in s
    # Autostart Run key added, with --no-window.
    assert "CurrentVersion\\Run" in s and "TailCam" in s
    assert "--no-window" in s


def test_windows_shortcut_ps_quote_escapes():
    from tailcam.desktop.windows_shortcut import _ps_single_quote

    assert _ps_single_quote("a'b") == "'a''b'"
    assert _ps_single_quote(r"C:\x") == r"'C:\x'"


def test_windows_shortcut_no_autostart(tmp_path, monkeypatch):
    from pathlib import Path as _P

    from tailcam.desktop import windows_shortcut as ws
    monkeypatch.setattr(ws, "_pythonw", lambda python=None: _P("pythonw.exe"))
    monkeypatch.setattr(ws, "_icon_path", lambda home=None: None)
    captured = {}
    monkeypatch.setattr(ws, "_run_ps", lambda script: captured.setdefault("s", script))
    ws.install_shortcut(home=tmp_path, autostart=False)
    # No Run-key write when autostart is off.
    assert "Set-ItemProperty" not in captured["s"]


def test_cli_app_install_on_windows(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from tailcam import cli
    from tailcam.desktop import windows_shortcut as ws

    monkeypatch.setattr(cli.sys, "platform", "win32")
    calls = {}
    def fake_install(autostart=False):
        calls["install"] = autostart
        return tmp_path / "TailCam.lnk"

    def fake_uninstall():
        calls["uninstall"] = True
        return True

    monkeypatch.setattr(ws, "install_shortcut", fake_install)
    monkeypatch.setattr(ws, "uninstall_shortcut", fake_uninstall)
    r = CliRunner().invoke(cli.app, ["app", "install", "--autostart"])
    assert r.exit_code == 0, r.output
    assert calls["install"] is True
    r = CliRunner().invoke(cli.app, ["app", "uninstall"])
    assert r.exit_code == 0
    assert calls["uninstall"] is True
