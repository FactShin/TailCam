"""Installer readiness uses only bounded loopback metadata requests."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tailcam import __version__
from tailcam.config import AppConfig
from tailcam.node import NodeConfigError
from tailcam.service import readiness


@pytest.fixture
def metadata_only(monkeypatch):
    # These deadline tests measure stalled network I/O, not first-run SQLite
    # initialization (which Windows antivirus can delay significantly).
    monkeypatch.setattr(readiness.AppConfig, "load", lambda: AppConfig())
    monkeypatch.setattr(
        readiness, "Store",
        lambda: SimpleNamespace(get_node_id=lambda: "da29f349-83ed-4f2d-bb58-564999c0a1c1"),
    )


@pytest.fixture
def startup_server(store):
    config = AppConfig()
    config.node.roles = []
    state = {
        "body": {"version": __version__, "node_id": store.get_node_id(), "node_roles": []},
        "status": 200,
        "paths": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["paths"].append(self.path)
            body = state["body"]
            if callable(body):
                body = body()
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(state["status"])
            if state["status"] == 302:
                self.send_header("Location", "http://not-loopback.invalid/credentials")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    config.server.port = server.server_port
    config.save()
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_ready_checks_only_local_system_metadata(startup_server, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://not-loopback.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://not-loopback.invalid:3128")
    assert readiness.wait_ready(timeout_seconds=1)
    assert startup_server["paths"] == ["/api/system"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "old-release"),
        ("node_id", "da29f349-83ed-4f2d-bb58-564999c0a1c1"),
        ("node_id", "not-a-uuid"),
        ("node_roles", ["capture"]),
        ("node_roles", None),
        ("node_roles", "hub"),
        ("node_roles", ["capture", "capture"]),
        ("node_roles", ["unknown"]),
    ],
)
def test_wrong_running_installation_never_passes(startup_server, field, value):
    startup_server["body"][field] = value
    assert not readiness.wait_ready(timeout_seconds=0.05, poll_interval=0.01)


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", b"x" * 65537], ids=["invalid-json", "array", "oversize"],
)
def test_invalid_or_oversize_metadata_fails_bounded(startup_server, body):
    startup_server["body"] = body
    assert not readiness.wait_ready(timeout_seconds=0.05, poll_interval=0.01)


@pytest.mark.parametrize("status", [302, 401, 500])
def test_error_and_redirect_never_count_as_startup(startup_server, status, monkeypatch):
    import socket

    real_connect = socket.create_connection
    destinations = []

    def only_loopback(address, *args, **kwargs):
        destinations.append(address[0])
        assert address[0] == "127.0.0.1"
        return real_connect(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", only_loopback)
    startup_server["status"] = status
    assert not readiness.wait_ready(timeout_seconds=0.05, poll_interval=0.01)
    assert destinations


def test_startup_retries_until_installed_roles_are_active(startup_server):
    correct = startup_server["body"]
    # Change after the first response, not after a wall-clock timer: slow CI
    # startup must still exercise the mismatched-role response and retry.
    startup_server["body"] = lambda: (
        dict(correct, node_roles=["capture"]) if len(startup_server["paths"]) == 1 else correct
    )
    assert readiness.wait_ready(timeout_seconds=2, poll_interval=0.01)
    assert len(startup_server["paths"]) == 2


def test_explicit_loopback_overrides_saved_ipv6_bind_for_container(startup_server):
    config = AppConfig.load()
    config.server.host = "::"
    config.save()
    assert readiness.wait_ready(timeout_seconds=1, host="127.0.0.1")


@pytest.mark.parametrize("host", ["example.com", "0.0.0.0", "127.0.0.1@evil.invalid"])
def test_probe_cannot_target_remote_host(host):
    with pytest.raises(ValueError, match="loopback"):
        readiness.wait_ready(host=host)


@pytest.mark.parametrize("timeout", [0, -1, 301, float("inf"), float("nan")])
def test_timeout_is_finite_and_bounded(timeout):
    with pytest.raises(ValueError, match="timeout"):
        readiness.wait_ready(timeout_seconds=timeout)


def test_unavailable_service_fails_within_deadline(metadata_only, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr("urllib.request.OpenerDirector.open", unavailable)
    start = time.monotonic()
    assert not readiness.wait_ready(timeout_seconds=0.05, poll_interval=0.01)
    assert time.monotonic() - start < 1


def test_cli_configuration_errors_do_not_print_secrets(monkeypatch, capsys):
    def invalid(**kwargs):
        raise NodeConfigError("secret-token-in-config")

    monkeypatch.setattr(readiness, "wait_ready", invalid)
    monkeypatch.setattr("sys.argv", ["readiness", "--timeout", "1"])
    assert readiness.main() == 1
    output = capsys.readouterr()
    assert "secret-token" not in output.out + output.err
    assert "saved configuration" in output.err


def test_stalled_http_headers_cannot_extend_installer_deadline(metadata_only, monkeypatch):
    release = threading.Event()

    def stalled(*args, **kwargs):
        release.wait(timeout=2)
        raise ConnectionError("stalled HTTP headers")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", stalled)
    start = time.monotonic()
    try:
        assert not readiness.wait_ready(timeout_seconds=0.05)
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
