"""Bounded, local startup verification for native and container installers."""

from __future__ import annotations

import argparse
import json
import math
import queue
import sqlite3
import sys
import threading
import time
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import (
    HTTPDefaultErrorHandler,
    HTTPErrorProcessor,
    HTTPHandler,
    HTTPRedirectHandler,
    OpenerDirector,
)

from tailcam import __version__
from tailcam.config import AppConfig
from tailcam.node import NodeConfigError, validate_roles
from tailcam.persistence.store import Store

_MAX_RESPONSE = 65536


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_opener() -> OpenerDirector:
    # build_opener also creates an unused HTTPS handler, loading the platform
    # certificate store before this HTTP-only probe can start its deadline.
    # Explicit handlers avoid TLS setup and environment proxy/other protocols.
    opener = OpenerDirector()
    for handler in (HTTPHandler(), HTTPDefaultErrorHandler(), HTTPErrorProcessor(), _NoRedirect()):
        opener.add_handler(handler)
    return opener


def wait_ready(
    *, timeout_seconds: float = 30, poll_interval: float = 0.25, host: str | None = None
) -> bool:
    """Verify the installed version, node UUID and active saved roles on loopback.

    No environment proxy, redirect, camera discovery or model health probe is
    used. A service bound exclusively to a non-loopback interface cannot pass.
    """
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
        raise ValueError("Startup timeout must be greater than 0 and at most 300 seconds")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("Poll interval must be positive")
    if host is not None and host not in {"127.0.0.1", "::1"}:
        raise ValueError("Startup host must be a loopback literal")
    config = AppConfig.load()
    expected_roles = validate_roles(config.node.roles)
    expected_id = Store().get_node_id()
    if host is None:
        host = "::1" if config.server.host in {"::", "::1"} else "127.0.0.1"
    host = "[::1]" if host == "::1" else host
    url = f"http://{host}:{config.server.port}/api/system"
    opener = _http_opener()
    deadline = time.monotonic() + timeout_seconds
    def probe() -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            with opener.open(url, timeout=min(2, remaining)) as response:
                if response.status != 200:
                    raise ValueError("Unexpected response status")
                # read1 returns available bytes instead of waiting for the
                # whole response, so a trickling response cannot reset a
                # per-socket timeout indefinitely beyond our deadline.
                payload = bytearray()
                while time.monotonic() < deadline and len(payload) <= _MAX_RESPONSE:
                    chunk = response.read1(min(4096, _MAX_RESPONSE + 1 - len(payload)))
                    if not chunk:
                        break
                    payload.extend(chunk)
                if time.monotonic() >= deadline or len(payload) > _MAX_RESPONSE:
                    raise ValueError("Startup response exceeded its bounds")
                data = json.loads(payload)
            if (
                isinstance(data, dict)
                and data.get("version") == __version__
                and data.get("node_id") == expected_id
                and validate_roles(data.get("node_roles")) == expected_roles
            ):
                return True
        except (OSError, URLError, HTTPException, ValueError):
            pass
        return False

    while time.monotonic() < deadline:
        outcome: queue.Queue[bool] = queue.Queue(maxsize=1)
        worker = threading.Thread(target=lambda result=outcome: result.put(probe()), daemon=True)
        worker.start()
        # A peer can trickle HTTP headers to evade socket timeouts. This wall-
        # clock wait bounds the caller even in that case; daemon I/O cannot
        # keep the readiness command alive after the deadline.
        try:
            if outcome.get(timeout=max(0, deadline - time.monotonic())):
                return True
        except queue.Empty:
            return False
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify TailCam startup on loopback")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--host", choices=("127.0.0.1", "::1"))
    args = parser.parse_args()
    try:
        ready = wait_ready(timeout_seconds=args.timeout, host=args.host)
    except (NodeConfigError, OSError, ValueError, sqlite3.Error):
        print(
            "Cannot verify startup: check the saved configuration and node database.",
            file=sys.stderr,
        )
        return 1
    if not ready:
        print(
            "TailCam startup was not verified on loopback. The running version, node identity "
            "and active roles must match this installation. Check service logs and the bind port.",
            file=sys.stderr,
        )
        return 1
    print(f"TailCam {__version__} startup verified on loopback.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
