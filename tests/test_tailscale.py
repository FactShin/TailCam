import json
import subprocess
from types import SimpleNamespace

from tailcam.tailscale.client import TAILCAM_APP_CAPABILITY, TailscaleClient

_STATUS_RUNNING = {
    "BackendState": "Running",
    "Self": {
        "TailscaleIPs": ["100.101.102.103", "fd7a::1"],
        "DNSName": "mybox.tailnet-abc.ts.net.",
    },
}


def _fake_run(stdout="", returncode=0):
    def runner(*args, **kwargs):
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    return runner


def _force_absent(monkeypatch):
    # No CLI on PATH and none of the known absolute install paths exist.
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: None)
    monkeypatch.setattr("tailcam.tailscale.client.os.path.isfile", lambda _p: False)


def test_not_installed(monkeypatch):
    _force_absent(monkeypatch)
    st = TailscaleClient().status()
    assert st.installed is False
    assert st.running is False


def test_resolves_binary_from_known_path(monkeypatch):
    # Not on PATH (e.g. launchd's minimal PATH) but installed at a known location.
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: None)
    target = "/opt/homebrew/bin/tailscale"
    monkeypatch.setattr("tailcam.tailscale.client.os.path.isfile", lambda p: p == target)
    monkeypatch.setattr("tailcam.tailscale.client.os.access", lambda p, _m: p == target)
    monkeypatch.setattr(subprocess, "run", _fake_run(json.dumps(_STATUS_RUNNING)))
    st = TailscaleClient().status()
    assert st.installed is True
    assert st.running is True


def test_status_running(monkeypatch):
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
    monkeypatch.setattr(subprocess, "run", _fake_run(json.dumps(_STATUS_RUNNING)))
    st = TailscaleClient().status()
    assert st.running is True
    assert st.ipv4 == "100.101.102.103"
    assert st.magic_dns == "mybox.tailnet-abc.ts.net"


def test_status_stopped(monkeypatch):
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
    monkeypatch.setattr(
        subprocess, "run", _fake_run(json.dumps({"BackendState": "Stopped", "Self": {}}))
    )
    st = TailscaleClient().status()
    assert st.installed is True
    assert st.running is False


def test_access_url_prefers_magicdns_when_served(monkeypatch):
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
    monkeypatch.setattr(subprocess, "run", _fake_run(json.dumps(_STATUS_RUNNING)))
    client = TailscaleClient()
    # Port 443 -> root URL (no port shown).
    assert client.access_url(8088, served=True, https_port=443) == "https://mybox.tailnet-abc.ts.net/"
    # Non-443 serve port -> URL includes the port.
    assert (
        client.access_url(8088, served=True, https_port=8443)
        == "https://mybox.tailnet-abc.ts.net:8443/"
    )
    assert client.access_url(8088, served=False) == "http://100.101.102.103:8088/"


def test_access_url_falls_back_to_localhost(monkeypatch):
    _force_absent(monkeypatch)
    assert TailscaleClient().access_url(8088, served=False) == "http://localhost:8088/"


def test_serve_accepts_app_capabilities_when_supported(monkeypatch):
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
    calls = []

    def runner(args, **kwargs):
        calls.append(args[1:])
        if args[1:] == ["serve", "--help"]:
            return SimpleNamespace(stdout="  --accept-app-caps string", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", runner)

    assert TailscaleClient().serve(local_port=8088, https_port=8443) is True

    assert calls[-1] == [
        "serve",
        "--bg",
        f"--accept-app-caps={TAILCAM_APP_CAPABILITY}",
        "--https=8443",
        "localhost:8088",
    ]


def test_serve_falls_back_without_app_capabilities_on_older_cli(monkeypatch):
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
    calls = []

    def runner(args, **kwargs):
        calls.append(args[1:])
        if args[1:] == ["serve", "--help"]:
            return SimpleNamespace(stdout="usage: tailscale serve", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", runner)
    client = TailscaleClient()

    assert client.app_capabilities_supported() is False
    assert client.serve(local_port=8088, https_port=8443) is True
    assert calls[-1] == ["serve", "--bg", "--https=8443", "localhost:8088"]


def test_status_reports_app_capability_support(monkeypatch):
    monkeypatch.setattr("tailcam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")

    def runner(args, **kwargs):
        if args[1:] == ["status", "--json"]:
            return SimpleNamespace(stdout=json.dumps(_STATUS_RUNNING), stderr="", returncode=0)
        if args[1:] == ["serve", "--help"]:
            return SimpleNamespace(stdout="--accept-app-caps string", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr(subprocess, "run", runner)

    assert TailscaleClient().status().app_capabilities_supported is True
