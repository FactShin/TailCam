from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from tailcam.management import runtime_probe


@pytest.fixture
def mock_endpoint(monkeypatch):
    original_client = httpx.AsyncClient

    def install(handler):
        calls = []
        options = {}

        def handle(request):
            calls.append(request)
            response = handler(request)
            # json=/content= mock responses have already consumed their stream;
            # expose their bytes as an unread stream like a network transport.
            if response.is_stream_consumed:
                return httpx.Response(
                    response.status_code, headers=response.headers,
                    stream=httpx.ByteStream(response.content),
                )
            return response

        def client(**kwargs):
            options.update(kwargs)
            return original_client(transport=httpx.MockTransport(handle), **kwargs)

        monkeypatch.setattr(runtime_probe.httpx, "AsyncClient", client)
        return calls, options

    return install


@pytest.mark.parametrize(
    ("selected", "installed", "state"),
    [
        ("llava", "llava:latest", "ready"),
        ("llava:latest", "llava", "ready"),
        ("llava", "llava:13b", "unavailable"),
        ("llava:7b", "llava:7b-q4", "unavailable"),
        ("llava", "llava-next:latest", "unavailable"),
        ("registry:5000/org/llava", "registry:5000/org/llava:latest", "ready"),
    ],
)
def test_exact_model_identity_and_latest_alias(mock_endpoint, selected, installed, state):
    mock_endpoint(lambda _: httpx.Response(200, json={"models": [{"name": installed}]}))
    result = runtime_probe._probe_in_process("http://ollama:11434", selected)
    assert result[0] == state
    if state == "ready":
        assert result[1] == "ollama.model_available"
        assert "inference and vision support were not tested" in result[2]
    else:
        assert result[1] == "ollama.model_missing"


def test_only_tags_get_with_bounded_timeout_and_environment_proxies_disabled(
    mock_endpoint, monkeypatch,
):
    monkeypatch.setenv("HTTP_PROXY", "http://user:proxy-secret@proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:proxy-secret@proxy.invalid:8080")
    calls, options = mock_endpoint(lambda _: httpx.Response(200, json={"models": []}))
    result = runtime_probe._probe_in_process("https://ollama.example/proxy/", "llava")
    assert result[1] == "ollama.model_missing"
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert str(calls[0].url) == "https://ollama.example/proxy/api/tags"
    assert calls[0].content == b""
    assert "authorization" not in calls[0].headers
    assert calls[0].headers["accept-encoding"] == "identity"
    assert options == {"timeout": 2.0, "trust_env": False, "follow_redirects": False}


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 407, 500])
def test_redirects_and_error_responses_are_not_followed_or_exposed(mock_endpoint, status):
    secret = "upstream-password"
    calls, _ = mock_endpoint(lambda _: httpx.Response(
        status, headers={"Location": f"http://user:{secret}@elsewhere.invalid/"},
        text=secret,
    ))
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[:2] == ("unavailable", "ollama.unreachable")
    assert len(calls) == 1
    assert secret not in str(result)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/key", "ftp://ollama", "http://", "http://ollama:99999",
        "http://[invalid", "http://user:secret@ollama", "http://ollama?token=secret",
        "http://ollama/#secret", "http://ollama/\r\nAuthorization:secret",
        "http://ollama\\@elsewhere", "http://ollama:0",
    ],
)
def test_invalid_or_credential_bearing_urls_never_open_a_connection(mock_endpoint, url):
    calls, _ = mock_endpoint(lambda _: pytest.fail("Invalid URL must not reach the network"))
    result = runtime_probe._probe_in_process(url, "llava")
    assert result[:2] == ("unavailable", "ollama.invalid_url")
    assert "secret" not in str(result)
    assert calls == []


@pytest.mark.parametrize(
    "model", ["", "llava:", ":latest", "llava one", "llava\nsecret", "a" * 257],
)
def test_invalid_selected_model_never_opens_connection(mock_endpoint, model):
    calls, _ = mock_endpoint(lambda _: pytest.fail("Invalid model must not reach the network"))
    result = runtime_probe._probe_in_process("http://ollama", model)
    assert result[:2] == ("unavailable", "ollama.invalid_model")
    assert "secret" not in str(result)
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [
        None, [], "secret", {}, {"models": None}, {"models": {}},
        {"models": ["secret"]}, {"models": [{}]}, {"models": [{"name": 1}]},
        {"models": [{"name": ""}]}, {"models": [{"name": "has spaces"}]},
        {"models": [{"name": "llava"}, {"name": "secret\n"}]},
    ],
)
def test_malformed_model_lists_never_report_ready_or_echo_server_data(mock_endpoint, payload):
    mock_endpoint(lambda _: httpx.Response(200, content=json.dumps(payload)))
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[:2] == ("unavailable", "ollama.invalid_response")
    assert "secret" not in str(result)


@pytest.mark.parametrize(
    "body", [b"secret", b"\xff", b"[" * 2000],
    ids=["invalid-json", "invalid-utf8", "deeply-nested"],
)
def test_invalid_encoding_json_and_excessive_nesting_fail_safely(mock_endpoint, body):
    mock_endpoint(lambda _: httpx.Response(200, content=body))
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[:2] == ("unavailable", "ollama.invalid_response")
    assert "secret" not in str(result)


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadTimeout, OSError])
def test_network_exception_details_never_escape(mock_endpoint, exception):
    def fail(_):
        raise exception("http://user:secret@proxy.invalid")

    mock_endpoint(fail)
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[0] == "unavailable"
    assert result[1] == (
        "ollama.timeout" if exception is httpx.ReadTimeout else "ollama.unreachable"
    )
    assert "secret" not in str(result)
    assert "proxy.invalid" not in str(result)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def test_stream_size_is_capped_even_without_content_length(mock_endpoint):
    stream = Chunks([b"x" * runtime_probe._MAX_BODY_BYTES, b"x", b"must-not-read"])
    mock_endpoint(lambda _: httpx.Response(200, stream=stream))
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[:2] == ("unavailable", "ollama.response_too_large")
    assert stream.read == 2
    assert stream.closed


def test_oversized_content_length_rejects_before_body_read(mock_endpoint):
    stream = Chunks([b"must-not-read"])
    mock_endpoint(lambda _: httpx.Response(
        200, headers={"Content-Length": str(runtime_probe._MAX_BODY_BYTES + 1)}, stream=stream,
    ))
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[:2] == ("unavailable", "ollama.response_too_large")
    assert stream.read == 0
    assert stream.closed


def test_model_item_count_is_capped(mock_endpoint):
    payload = {"models": [{"name": "llava"}] * (runtime_probe._MAX_MODELS + 1)}
    mock_endpoint(lambda _: httpx.Response(200, json=payload))
    assert runtime_probe._probe_in_process("http://ollama", "llava")[:2] == (
        "unavailable", "ollama.response_too_large",
    )


def test_trickling_body_stops_at_elapsed_deadline(mock_endpoint, monkeypatch):
    clock = [0.0]

    def chunks():
        for chunk in [b'{"models":', b"[]}", b"must-not-read"]:
            clock[0] += 1.1
            yield chunk

    stream = Chunks(chunks())
    mock_endpoint(lambda _: httpx.Response(200, stream=stream))
    monkeypatch.setattr(runtime_probe.time, "monotonic", lambda: clock[0])
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[:2] == ("unavailable", "ollama.timeout")
    assert stream.read == 2
    assert stream.closed


def test_compressed_response_is_rejected_without_expansion(mock_endpoint):
    stream = Chunks([b"must-not-decompress"])
    mock_endpoint(lambda _: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, stream=stream,
    ))
    result = runtime_probe._probe_in_process("http://ollama", "llava")
    assert result[:2] == ("unavailable", "ollama.invalid_response")
    assert stream.read == 0


def test_wall_clock_deadline_cancels_stalled_headers_without_using_proxy(monkeypatch):
    release = threading.Event()
    server_started = threading.Event()
    seen = []

    class StalledHeaders(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            self.wfile.flush()
            # Keep resetting a per-socket read timeout without completing the
            # headers. The parent's deadline must still stop the child promptly.
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline and not release.wait(0.025):
                try:
                    self.wfile.write(b".")
                    self.wfile.flush()
                except OSError:
                    break

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), StalledHeaders)

    def serve():
        server_started.set()
        server.serve_forever(poll_interval=0.01)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    monkeypatch.setenv("HTTP_PROXY", "http://user:proxy-secret@127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://user:proxy-secret@127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        assert server_started.wait(3), "Loopback server did not start"
        assert runtime_probe._TIMEOUT_SECONDS == 2.0
        started = time.monotonic()
        result = runtime_probe.probe_ollama(f"http://127.0.0.1:{server.server_port}", "llava")
        elapsed = time.monotonic() - started
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)
    assert result[:2] == ("unavailable", "ollama.timeout")
    assert "proxy-secret" not in str(result)
    assert seen == ["/api/tags"]
    # Allow scheduler overhead while remaining well below the six-second
    # trickle that would defeat a per-socket timeout without cancellation.
    assert elapsed < 4.0


def test_parent_passes_request_only_on_stdin_and_accepts_fixed_worker_result(monkeypatch):
    expected = runtime_probe._result("ollama.model_available")
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(expected)))
    monkeypatch.setattr(runtime_probe, "run_hidden", runner)

    url, model = "https://ollama.example/reverse-proxy", "llava:7b"
    assert runtime_probe.probe_ollama(url, model) == expected
    command = runner.call_args.args[0]
    assert command[1:] == ["-m", "tailcam.management.runtime_probe", "--inventory-worker"]
    assert url not in command and model not in command
    kwargs = runner.call_args.kwargs
    assert json.loads(kwargs["input"]) == {"base_url": url, "model": model}
    assert kwargs["timeout"] == 2.0
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE
    assert "env" not in kwargs


@pytest.mark.parametrize(("url", "model", "code"), [
    ("http://user:secret@ollama", "llava", "ollama.invalid_url"),
    ("file:///secret", "llava", "ollama.invalid_url"),
    ("http://ollama", "", "ollama.invalid_model"),
    ("http://ollama", "llava\nsecret", "ollama.invalid_model"),
])
def test_parent_validates_request_before_spawning(monkeypatch, url, model, code):
    runner = Mock(side_effect=AssertionError("Invalid request started a process"))
    monkeypatch.setattr(runtime_probe, "run_hidden", runner)
    assert runtime_probe.probe_ollama(url, model)[1] == code
    runner.assert_not_called()


@pytest.mark.parametrize("output", [
    "secret traceback", "null", "{}", '["ready"]',
    '["ready", "ollama.unknown", "secret"]',
    '["ready", "ollama.model_available", "secret"]',
    '["ready", "ollama.timeout", "The Ollama inventory probe timed out."]',
    "x" * (runtime_probe._MAX_WORKER_OUTPUT + 1),
])
def test_parent_rejects_malformed_worker_output_without_echoing_it(monkeypatch, output):
    monkeypatch.setattr(runtime_probe, "run_hidden", Mock(return_value=SimpleNamespace(
        returncode=0, stdout=output,
    )))
    assert runtime_probe.probe_ollama("http://ollama", "llava") == runtime_probe._result(
        "ollama.probe_failed",
    )


def test_parent_does_not_trust_output_from_failed_worker(monkeypatch):
    monkeypatch.setattr(runtime_probe, "run_hidden", Mock(return_value=SimpleNamespace(
        returncode=1, stdout=json.dumps(runtime_probe._result("ollama.model_available")),
    )))
    assert runtime_probe.probe_ollama("http://ollama", "llava")[1] == "ollama.probe_failed"


@pytest.mark.parametrize(("error", "code"), [
    (OSError("private executable path /secret"), "ollama.probe_failed"),
    (subprocess.TimeoutExpired("private worker", 2, output="secret"), "ollama.timeout"),
])
def test_parent_process_errors_are_sanitized(monkeypatch, error, code):
    monkeypatch.setattr(runtime_probe, "run_hidden", Mock(side_effect=error))
    result = runtime_probe.probe_ollama("http://ollama", "llava")
    assert result[1] == code
    assert "secret" not in str(result) and "private" not in str(result)


def test_windows_service_worker_uses_sibling_console_python(monkeypatch, tmp_path):
    pythonw = tmp_path / "pythonw.exe"
    console = tmp_path / "python.exe"
    console.touch()
    monkeypatch.setattr(runtime_probe.sys, "executable", str(pythonw))
    assert runtime_probe._worker_command() == [
        str(console), "-m", "tailcam.management.runtime_probe", "--inventory-worker",
    ]
    console.unlink()
    runner = Mock(side_effect=AssertionError("pythonw must not be used for IPC"))
    monkeypatch.setattr(runtime_probe, "run_hidden", runner)
    assert runtime_probe.probe_ollama("http://ollama", "llava")[1] == "ollama.probe_failed"
    runner.assert_not_called()


def test_frozen_executable_is_not_recursively_launched(monkeypatch):
    monkeypatch.setattr(runtime_probe.sys, "frozen", True, raising=False)
    runner = Mock(side_effect=AssertionError("Frozen GUI must not be launched"))
    monkeypatch.setattr(runtime_probe, "run_hidden", runner)
    assert runtime_probe.probe_ollama("http://ollama", "llava")[:2] == (
        "unchecked", "ollama.probe_unsupported",
    )
    runner.assert_not_called()


def test_parent_kills_and_reaps_worker_stuck_in_dns_executor(monkeypatch, tmp_path):
    marker = tmp_path / "dns-entered"
    # Reproduce asyncio.run waiting for a cancelled executor lookup, without
    # any OS DNS or connection. The resolver cannot finish during this test.
    script = f"""
import asyncio, socket, time
from pathlib import Path
from tailcam.management import runtime_probe
def blocked_lookup(*args, **kwargs):
    Path({str(marker)!r}).write_text('entered')
    time.sleep(30)
    raise socket.gaierror('isolated mock lookup failed')
async def inventory(*args):
    await asyncio.get_running_loop().getaddrinfo('blocked.invalid', 11434)
    return runtime_probe._result('ollama.model_missing')
socket.getaddrinfo = blocked_lookup
runtime_probe._fetch_inventory = inventory
runtime_probe._TIMEOUT_SECONDS = 0.5
raise SystemExit(runtime_probe._worker_main())
"""
    monkeypatch.setattr(runtime_probe, "_worker_command", lambda: [sys.executable, "-c", script])
    children = []
    real_popen = subprocess.Popen

    def observe_child(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", observe_child)
    started = time.monotonic()
    result = runtime_probe.probe_ollama("http://blocked.invalid:11434", "llava")
    elapsed = time.monotonic() - started

    assert result[:2] == ("unavailable", "ollama.timeout")
    assert marker.read_text() == "entered"
    assert len(children) == 1
    assert children[0].returncode is not None
    assert children[0].wait(timeout=0) == children[0].returncode
    assert elapsed < 4.0


def test_real_worker_cli_returns_safe_inventory_result_from_loopback():
    seen = []

    class Inventory(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            body = json.dumps({"models": [{"name": "llava:latest"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Inventory)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    worker.start()
    try:
        # Protocol correctness is separate from the hard parent deadline test;
        # allow cold interpreter startup on every supported CI platform here.
        completed = runtime_probe.run_hidden(
            runtime_probe._worker_command(),
            input=json.dumps({
                "base_url": f"http://127.0.0.1:{server.server_port}", "model": "llava",
            }),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == list(runtime_probe._result("ollama.model_available"))
    assert seen == ["/api/tags"]
