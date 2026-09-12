"""A bounded Ollama inventory probe; it never generates or warms a model."""

from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

_TIMEOUT_SECONDS = 2.0
_MAX_BODY_BYTES = 64 * 1024
_MAX_MODELS = 256
_MAX_MODEL_LENGTH = 256
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z")
_TIMED_OUT = ("unavailable", "ollama.timeout", "The Ollama inventory probe timed out.")


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


def probe_ollama(base_url: str, model: str) -> tuple[str, str, str]:
    """Read the installed model names, returning only fixed, credential-safe text.

    Synchronous callers only: the management HTTP route runs in a worker thread.
    An async deadline cancels stalled headers as well as a slowly streaming body.
    Raw chunks avoid buffering to a requested chunk size or expanding compressed
    responses. Role checks and caching belong to the caller.
    """
    url = _tags_url(base_url)
    if url is None:
        return (
            "unavailable", "ollama.invalid_url",
            "Configure an HTTP or HTTPS Ollama URL without credentials, query or fragment.",
        )
    wanted = _model_identity(model)
    if wanted is None:
        return "unavailable", "ollama.invalid_model", "Configure a valid Ollama model name."
    return asyncio.run(_bounded_probe(url, wanted))


async def _bounded_probe(url: str, wanted: str) -> tuple[str, str, str]:
    try:
        return await asyncio.wait_for(_fetch_inventory(url, wanted), timeout=_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return _TIMED_OUT


async def _fetch_inventory(url: str, wanted: str) -> tuple[str, str, str]:
    invalid = ("unavailable", "ollama.invalid_response", "Ollama returned an invalid model list.")
    oversized = (
        "unavailable", "ollama.response_too_large", "Ollama's model list exceeded the probe limit.",
    )
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
                    return (
                        "unavailable", "ollama.unreachable",
                        "Ollama did not return a successful model-list response.",
                    )
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
        return "unavailable", "ollama.invalid_url", "Configure a valid HTTP or HTTPS Ollama URL."
    except (httpx.HTTPError, OSError):
        return "unavailable", "ollama.unreachable", "The Ollama inventory endpoint is unavailable."
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
        return (
            "ready", "ollama.model_available",
            "The selected model is installed; inference and vision support were not tested.",
        )
    return "unavailable", "ollama.model_missing", "The selected model is not installed in Ollama."
