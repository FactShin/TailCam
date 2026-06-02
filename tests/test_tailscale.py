import json
import subprocess
from types import SimpleNamespace

from anycam.tailscale.client import TailscaleClient

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


def test_not_installed(monkeypatch):
    monkeypatch.setattr("anycam.tailscale.client.shutil.which", lambda _b: None)
    st = TailscaleClient().status()
    assert st.installed is False
    assert st.running is False


def test_status_running(monkeypatch):
    monkeypatch.setattr("anycam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
    monkeypatch.setattr(subprocess, "run", _fake_run(json.dumps(_STATUS_RUNNING)))
    st = TailscaleClient().status()
    assert st.running is True
    assert st.ipv4 == "100.101.102.103"
    assert st.magic_dns == "mybox.tailnet-abc.ts.net"


def test_status_stopped(monkeypatch):
    monkeypatch.setattr("anycam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
    monkeypatch.setattr(
        subprocess, "run", _fake_run(json.dumps({"BackendState": "Stopped", "Self": {}}))
    )
    st = TailscaleClient().status()
    assert st.installed is True
    assert st.running is False


def test_access_url_prefers_magicdns_when_served(monkeypatch):
    monkeypatch.setattr("anycam.tailscale.client.shutil.which", lambda _b: "/usr/bin/tailscale")
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
    monkeypatch.setattr("anycam.tailscale.client.shutil.which", lambda _b: None)
    assert TailscaleClient().access_url(8088, served=False) == "http://localhost:8088/"
