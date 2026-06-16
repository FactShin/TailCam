#!/usr/bin/env python3
"""Smoke-test the built TailCam sidecar without touching host Tailscale."""

from __future__ import annotations

import json
import os
import platform
import runpy
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

BUILD_SCRIPT = Path(__file__).with_name("build-sidecar.py")
namespace = runpy.run_path(str(BUILD_SCRIPT))

ROOT = Path(__file__).resolve().parents[2]
BINARIES = ROOT / "desktop" / "src-tauri" / "binaries"
HEALTH_PATH = "/api/v1/node/health"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def sidecar_path() -> Path:
    explicit = os.environ.get("TAILCAM_NODE_SIDECAR")
    if explicit:
        return Path(explicit)
    triple = namespace["target_triple"]()
    return BINARIES / namespace["sidecar_filename"](triple, platform.system())


def get_json(url: str) -> dict | list:
    with urlopen(url, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for(url: str, deadline: float) -> dict:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            data = get_json(url)
            if isinstance(data, dict):
                return data
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise SystemExit(f"Timed out waiting for {url}: {last_error}")


def main() -> None:
    sidecar = sidecar_path()
    if not sidecar.exists():
        raise SystemExit(f"Missing sidecar: {sidecar}. Run desktop/scripts/build-sidecar.py first.")

    port = free_port()
    with tempfile.TemporaryDirectory(prefix="tailcam-sidecar-") as tmp:
        root = Path(tmp)
        config_dir = root / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text(
            "[tailscale]\nauto_serve = false\n\n[peers]\nauto_discover = false\n",
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "TAILCAM_SYNTHETIC": "1",
            "TAILCAM_DATA_DIR": str(root / "data"),
            "TAILCAM_CONFIG_DIR": str(config_dir),
        }
        proc = subprocess.Popen(
            [str(sidecar), "run", "--no-tailscale", "--port", str(port)],
            cwd=ROOT,
            env=env,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            system = wait_for(f"{base}/api/system", time.monotonic() + 30.0)
            if not system.get("version") or not system.get("host"):
                raise SystemExit(f"Invalid /api/system response: {system}")
            with urlopen(f"{base}/", timeout=2.0) as response:
                if response.status != 200:
                    raise SystemExit(f"Unexpected / status: {response.status}")
            cameras = get_json(f"{base}/api/cameras")
            if not isinstance(cameras, list):
                raise SystemExit(f"Invalid /api/cameras response: {cameras}")
            health = get_json(f"{base}{HEALTH_PATH}")
            if not isinstance(health, dict) or "camera_total" not in health:
                raise SystemExit(f"Invalid /api/v1/node/health response: {health}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)

    print(f"Smoke passed: {sidecar}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
