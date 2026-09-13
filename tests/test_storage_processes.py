"""Separate node processes exercise the production HTTP protocol on loopback."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

_SERVER = """
import os
from pathlib import Path
import uvicorn
from tailcam.config import AppConfig
from tailcam.web.app import create_app
from tailcam.web.context import AppContext
cfg = AppConfig()
cfg.node.roles = ['capture'] if os.environ['TEST_NODE'] == 'source' else ['storage']
cfg.tailscale.auto_serve = False
cfg.peers.auto_discover = False
cfg.ai.enabled = False
cfg.ai.base_url = 'http://127.0.0.1:1'
cfg.detection.enabled = False
cfg.motion.enabled = False
cfg.server.port = int(os.environ['TEST_PORT'])
ctx = AppContext(cfg)
if os.environ['TEST_NODE'] != 'source':
    root = str(Path(os.environ['TAILCAM_DATA_DIR']) / 'artifacts')
    ctx.storage_service.register_location(root, create=True)
uvicorn.run(create_app(cfg, context=ctx), host='127.0.0.1', port=cfg.server.port, log_level='error')
"""


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class NodeProcess:
    def __init__(self, root: Path, name: str, peer: str = ""):
        self.root, self.name, self.port, self.peer = root, name, _port(), peer
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None
        self.log = None
        self.client = httpx.Client(base_url=self.url, timeout=10, trust_env=False)

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        environment = {
            **os.environ,
            "TAILCAM_HOST": self.name,
            "TAILCAM_PEERS": self.peer,
            "TAILCAM_DATA_DIR": str(self.root / "data"),
            "TAILCAM_CONFIG_DIR": str(self.root / "config"),
            "TAILCAM_SYNTHETIC": "1",
            "TAILCAM_LOW_POWER": "0",
            "TEST_NODE": self.name,
            "TEST_PORT": str(self.port),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
        environment.pop("TAILCAM_CONFIG", None)
        self.log = (self.root / "server.log").open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-c", _SERVER],
            env=environment,
            stdout=self.log,
            stderr=self.log,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert self.process.poll() is None, (self.root / "server.log").read_text()
            try:
                response = self.client.get("/api/system", timeout=0.3)
                if response.status_code == 200:
                    self.identity = response.json()["node_id"]
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        pytest.fail("Isolated node did not become ready")

    def stop(self, *, crash=False):
        if self.process is not None:
            self.process.kill() if crash else self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None


def test_remote_snapshot_zero_local_and_receiver_crash_resume(tmp_path):
    owner = NodeProcess(tmp_path / "owner", "owner")
    source = NodeProcess(tmp_path / "source", "source", peer=owner.url)
    try:
        owner.start()
        source.start()
        # A source can resolve an explicitly configured owner before anyone
        # opens the fleet/cameras screen; queued work must recover unattended.
        destination = source.client.get("/api/v1/storage/destinations")
        assert destination.status_code == 200, destination.text
        assert owner.identity in {item["node_id"] for item in destination.json()["items"]}
        location = owner.client.get("/api/v1/storage/locations").json()["items"][0]
        policy = source.client.get("/api/v1/storage/policy").json()["policy"]
        policy.update(
            default_destination={"node_id": owner.identity, "location_id": location["location_id"]},
            zero_local_media=True,
        )
        saved = source.client.patch(
            "/api/v1/storage/policy", json={"policy": policy, "expected_revision": 1}
        )
        assert saved.status_code == 200, saved.text
        cameras = source.client.get("/api/cameras?scope=local").json()
        camera_id = cameras[0]["id"]
        snapshot = source.client.post(f"/api/cameras/{camera_id}/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        media_id = snapshot.json()["media_id"]
        owner_items = owner.client.get("/api/v1/artifacts").json()["items"]
        assert {item["kind"] for item in owner_items} == {"snapshot", "thumbnail"}
        assert all(item["origin_node_id"] == source.identity for item in owner_items)
        assert source.client.get(f"/media/{media_id}/file").content.startswith(b"\xff\xd8")
        assert not list((source.root / "data").rglob("*.jpg"))

        # Abrupt owner death after acknowledging the first chunk. The same
        # declaration/transfer resumes into one checksum-verified artifact.
        data = b"first" + b"second"
        artifact_id = str(uuid4())
        target = {"node_id": owner.identity, "location_id": location["location_id"]}
        declaration = {
            "artifact": {
                "artifact_id": artifact_id,
                "owner_node_id": owner.identity,
                "origin_node_id": source.identity,
                "kind": "export",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "created_at": time.time(),
                "updated_at": time.time(),
                "requested_destination": target,
                "policy_revision": 1,
            },
            "destination": target,
            "idempotency_key": str(uuid4()),
        }
        begun = owner.client.post("/api/v1/transfers", json=declaration)
        assert begun.status_code == 200, begun.text
        transfer_id = begun.json()["transfer_id"]
        url = f"/api/v1/transfers/{transfer_id}"
        response = owner.client.put(
            url + "/chunks?offset=0",
            content=b"first",
            headers={
                "X-Chunk-SHA256": hashlib.sha256(b"first").hexdigest(),
            },
        )
        assert response.json()["offset"] == 5
        owner.stop(crash=True)
        offline = source.client.get("/api/v1/fleet/artifacts").json()["items"]
        assert len(offline) == 2 and all(not item["owner_online"] for item in offline)
        owner.start()
        assert (
            owner.client.post("/api/v1/transfers", json=declaration).json()["transfer_id"]
            == transfer_id
        )
        assert owner.client.get(url).json()["offset"] == 5
        response = owner.client.put(
            url + "/chunks?offset=5",
            content=b"second",
            headers={
                "X-Chunk-SHA256": hashlib.sha256(b"second").hexdigest(),
            },
        )
        assert response.status_code == 200, response.text
        assert owner.client.post(url + "/commit").status_code == 200
        assert owner.client.get(f"/api/v1/artifacts/{artifact_id}/content").content == data
        assert len(owner.client.get("/api/v1/artifacts?kind=export").json()["items"]) == 1
    finally:
        source.stop()
        owner.stop()
        source.client.close()
        owner.client.close()
