"""Real HTTP with separate source/storage/AI processes and independent disks."""

import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(predicate, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            value = predicate()
            if value:
                return value
        except (httpx.HTTPError, KeyError, IndexError):
            pass
        time.sleep(0.1)
    pytest.fail("isolated capture process did not reach expected state")


@pytest.fixture
def process_fleet(tmp_path):
    processes = []

    def launch(name, port, *args):
        log = (tmp_path / f"{name}.log").open("w")
        root = tmp_path / name
        process = subprocess.Popen(
            [sys.executable, str(_ROOT / "tests/helpers/capture_node.py"),
             "--name", name, "--port", str(port), "--root", str(root), *args],
            env={**os.environ, "PYTHONPATH": str(_ROOT / "src"),
                 "NO_PROXY": "127.0.0.1,localhost"},
            stdout=log, stderr=subprocess.STDOUT,
        )
        processes.append((process, log))
        return root

    try:
        yield launch
    finally:
        for process, _ in reversed(processes):
            process.terminate()
        for process, log in reversed(processes):
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


@pytest.mark.parametrize("analysis_available", [False, True])
def test_timelapse_destination_and_rejection_across_processes(process_fleet, analysis_available):
    source_port, storage_port, ai_port = _port(), _port(), _port()
    source_url = f"http://127.0.0.1:{source_port}"
    storage_url = f"http://127.0.0.1:{storage_port}"
    ai_url = f"http://127.0.0.1:{ai_port}"
    ai_root = process_fleet("mock-ai", ai_port)
    storage_root = process_fleet(
        "storage-box", storage_port, "--peer", source_url,
        *(('--ai', ai_url) if analysis_available else ()),
    )
    source_root = process_fleet(
        # Explicit URL without a static-peer entry must discover a real owner.
        "source-pi", source_port, "--storage", storage_url,
    )
    with httpx.Client(timeout=10, trust_env=False) as http:
        _wait(lambda: http.get(f"{source_url}/api/cameras?scope=local").json())
        _wait(lambda: http.get(f"{storage_url}/api/cameras?scope=local").json())
        _wait(lambda: any(h["host"] == "storage-box" for h in
                         http.get(f"{source_url}/api/hosts").json()))
        _wait(lambda: any(h["host"] == "source-pi" for h in
                         http.get(f"{storage_url}/api/hosts").json()))
        cam = http.get(f"{source_url}/api/cameras?scope=local").json()[0]["id"]
        request = {"analysis_enabled": True, "analysis_cadence_seconds": 1,
                   "interval_seconds": 0.1, "output_fps": 10, "auto_smooth": False}
        # The same source action directly and through the storage dashboard.
        routes = [source_url, f"{storage_url}/proxy/source-pi"]
        for entry in routes:
            dashboard = source_url if entry == source_url else storage_url
            preflight = http.get(f"{entry}/api/cameras/{cam}/timelapse/preflight").json()
            assert preflight["capture_host"] == "storage-box"
            assert preflight["route_status"] == "reachable"
            assert preflight["capabilities"]["printer_analyzer"]["enabled"] == analysis_available
            response = http.post(f"{entry}/api/cameras/{cam}/timelapse/start", json=request)
            if not analysis_available:
                assert response.status_code == 409, response.text
                assert "storage node" in response.json()["detail"]
                continue
            assert response.status_code == 200, response.text
            info = response.json()
            assert info["host"] == "storage-box"
            assert info["source_host"] == "source-pi"
            assert info["proxy_prefix"] == (
                "/proxy/storage-box" if entry == source_url else ""
            )
            tl_id = info["id"]
            _wait(lambda tl_id=tl_id: http.get(f"{storage_url}/api/timelapse/{tl_id}")
                  .json()["frames_captured"] >= 3)
            events = _wait(lambda tl_id=tl_id: http.get(
                f"{storage_url}/api/timelapse/{tl_id}/analysis-events",
            ).json())
            assert events[0]["state"] == "healthy"
            owner_url = f"{dashboard}{info['proxy_prefix']}"
            response = http.post(f"{owner_url}/api/timelapse/{tl_id}/stop")
            assert response.status_code == 200
            _wait(lambda tl_id=tl_id: http.get(f"{storage_url}/api/timelapse/{tl_id}")
                  .json()["state"] == "complete")
            media = http.get(f"{owner_url}/timelapse/{tl_id}/file")
            assert media.status_code == 200 and len(media.content) > 100
        assert http.get(f"{source_url}/api/timelapse?scope=local").json() == []
        assert not list((source_root / "media").rglob("*.jpg"))
        assert not list((source_root / "media").rglob("*.mp4"))
        if analysis_available:
            assert list((storage_root / "media").rglob("*.mp4"))
            assert (ai_root / "requests.jsonl").read_text().count("test-printer") >= 2
            with sqlite3.connect(storage_root / "data/tailcam.db") as db:
                records = db.execute("SELECT frames_dir FROM timelapses").fetchall()
            assert len(records) == 2
            assert all(Path(row[0]).is_relative_to(storage_root / "media") for row in records)
        else:
            assert http.get(f"{storage_url}/api/timelapse?scope=local").json() == []
            assert not list((storage_root / "media").rglob("*.jpg"))
