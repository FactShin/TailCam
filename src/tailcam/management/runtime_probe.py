"""A bounded Ollama inventory probe; it never generates or warms a model."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from tailcam.proc import run as run_hidden

_TIMEOUT_SECONDS = 2.0
_MAX_BODY_BYTES = 64 * 1024
_MAX_MODELS = 256
_MAX_MODEL_LENGTH = 256
_MAX_WORKER_INPUT = 32 * 1024
_MAX_WORKER_OUTPUT = 4096
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")
_RESULTS = {
    "ollama.model_available": (
        "ready", "The selected model is installed; inference and vision support were not tested.",
    ),
    "ollama.model_missing": ("unavailable", "The selected model is not installed in Ollama."),
    "ollama.invalid_url": (
        "unavailable",
        "Configure an HTTP or HTTPS Ollama URL without credentials, query or fragment.",
    ),
    "ollama.invalid_model": ("unavailable", "Configure a valid Ollama model name."),
    "ollama.invalid_response": ("unavailable", "Ollama returned an invalid model list."),
    "ollama.response_too_large": (
        "unavailable", "Ollama's model list exceeded the probe limit.",
    ),
    "ollama.timeout": ("unavailable", "The Ollama inventory probe timed out."),
    "ollama.unreachable": ("unavailable", "The Ollama inventory endpoint is unavailable."),
    "ollama.probe_failed": (
        "unavailable", "The isolated Ollama inventory probe could not complete.",
    ),
    "ollama.probe_unsupported": (
        "unchecked", "Runtime inventory probes are unavailable in this packaged environment.",
    ),
}


def _result(code: str) -> tuple[str, str, str]:
    state, detail = _RESULTS[code]
    return state, code, detail


_TIMED_OUT = _result("ollama.timeout")


def _model_identity(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_MODEL_LENGTH
        or not _MODEL_NAME.fullmatch(value)
        or any(not part for part in value.split("/"))
    ):
        return None
    # A registry's port is not the model tag: only inspect the final segment.
    final = value.rsplit("/", 1)[-1]
    if ":" in final:
        if final.count(":") != 1 or not all(final.split(":")):
            return None
        return value
    return value + ":latest"


def _tags_url(value: str) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or "\\" in value
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or (parts.port is not None and not 0 < parts.port <= 65535)
        ):
            return None
        path = parts.path.rstrip("/") + "/api/tags"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    except ValueError:
        return None


def _worker_command() -> list[str] | None:
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        # Services use pythonw, whose standard streams can be absent. The
        # sibling console interpreter supports IPC; proc.run hides its window.
        executable = executable.with_name("python.exe")
        if not executable.is_file():
            return None
    return [str(executable), "-m", "tailcam.management.runtime_probe", "--inventory-worker"]


def probe_ollama(base_url: str, model: str) -> tuple[str, str, str]:
    """Run an explicit inventory check with a parent-enforced process deadline.

    OS DNS resolution may outlive async cancellation. A short-lived child lets
    subprocess.run kill and reap the entire resolver on timeout. Arguments go
    through stdin, and only fixed result messages cross back to the caller.
    Passive snapshots never call this function; role gating/cache live above it.
    """
    if _tags_url(base_url) is None:
        return _result("ollama.invalid_url")
    if _model_identity(model) is None:
        return _result("ollama.invalid_model")
    if getattr(sys, "frozen", False):
        return _result("ollama.probe_unsupported")
    command = _worker_command()
    if command is None:
        return _result("ollama.probe_failed")
    try:
        completed = run_hidden(
            command,
            input=json.dumps({"base_url": base_url, "model": model}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=_TIMEOUT_SECONDS,
        )
        if (
            completed.returncode != 0
            or not isinstance(completed.stdout, str)
            or len(completed.stdout) > _MAX_WORKER_OUTPUT
        ):
            return _result("ollama.probe_failed")
        value = json.loads(completed.stdout)
        if (
            isinstance(value, list) and len(value) == 3
            and all(isinstance(part, str) for part in value)
            and value[1] in _RESULTS
            and tuple(value) == _result(value[1])
        ):
            return _result(value[1])
    except subprocess.TimeoutExpired:
        return _TIMED_OUT
    except (OSError, ValueError, RecursionError):
        pass
    return _result("ollama.probe_failed")


def _probe_in_process(base_url: str, model: str) -> tuple[str, str, str]:
    """Worker implementation; not a safe deadline boundary for server callers."""
    url = _tags_url(base_url)
    if url is None:
        return _result("ollama.invalid_url")
    wanted = _model_identity(model)
    if wanted is None:
        return _result("ollama.invalid_model")
    return asyncio.run(_bounded_probe(url, wanted))


async def _bounded_probe(url: str, wanted: str) -> tuple[str, str, str]:
    try:
        return await asyncio.wait_for(_fetch_inventory(url, wanted), timeout=_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return _TIMED_OUT


async def _fetch_inventory(url: str, wanted: str) -> tuple[str, str, str]:
    invalid = _result("ollama.invalid_response")
    oversized = _result("ollama.response_too_large")
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, trust_env=False, follow_redirects=False,
        ) as client:
            async with client.stream(
                "GET", url, headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as response:
                if time.monotonic() >= deadline:
                    return _TIMED_OUT
                if response.status_code != 200:
                    return _result("ollama.unreachable")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return invalid
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        size = int(length)
                    except ValueError:
                        return invalid
                    if size < 0:
                        return invalid
                    if size > _MAX_BODY_BYTES:
                        return oversized
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if time.monotonic() >= deadline:
                        return _TIMED_OUT
                    if len(body) + len(chunk) > _MAX_BODY_BYTES:
                        return oversized
                    body.extend(chunk)
                if time.monotonic() >= deadline:
                    return _TIMED_OUT
        data = json.loads(body)
    except httpx.TimeoutException:
        return _TIMED_OUT
    except httpx.InvalidURL:
        return _result("ollama.invalid_url")
    except (httpx.HTTPError, OSError):
        return _result("ollama.unreachable")
    except (ValueError, RecursionError):
        return invalid

    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return invalid
    if len(data["models"]) > _MAX_MODELS:
        return oversized
    installed: set[str] = set()
    for item in data["models"]:
        if not isinstance(item, dict) or (name := _model_identity(item.get("name"))) is None:
            return invalid
        installed.add(name)
    if wanted in installed:
        return _result("ollama.model_available")
    return _result("ollama.model_missing")


def _worker_main() -> int:
    result = _result("ollama.probe_failed")
    try:
        raw = sys.stdin.read(_MAX_WORKER_INPUT + 1)
        if len(raw) <= _MAX_WORKER_INPUT:
            request = json.loads(raw)
            if (
                isinstance(request, dict)
                and set(request) == {"base_url", "model"}
                and isinstance(request["base_url"], str)
                and isinstance(request["model"], str)
            ):
                result = _probe_in_process(request["base_url"], request["model"])
    except Exception:
        # Neither tracebacks nor endpoint/response data belong on the IPC pipe.
        pass
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--inventory-worker"]:
        raise SystemExit(2)
    raise SystemExit(_worker_main())
