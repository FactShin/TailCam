"""Three isolated HTTP nodes: capture, canonical storage and restarted compute."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid5

import httpx
import pytest

_SERVER = """
import os
import time
from pathlib import Path
import uvicorn
from tailcam.config import AppConfig
from tailcam.web.app import create_app
from tailcam.web.context import AppContext
cfg = AppConfig()
name = os.environ['TEST_NODE']
cfg.node.roles = {'source':['capture'], 'owner':['storage'], 'compute':['analysis']}[name]
cfg.tailscale.auto_serve = False
cfg.peers.auto_discover = False
cfg.ai.enabled = False
cfg.ai.base_url = 'http://127.0.0.1:1'
cfg.detection.enabled = False
cfg.motion.enabled = False
cfg.plugins.load_dropins = False
cfg.jobs.lease_seconds = 2
cfg.jobs.poll_seconds = 0.1
cfg.server.port = int(os.environ['TEST_PORT'])
ctx = AppContext(cfg)
root = Path(os.environ['TEST_ROOT'])
if name == 'owner':
    ctx.storage_service.register_location(str(root / 'canonical'), create=True)
if name == 'compute':
    original_run = ctx.jobs.executor.run
    def counted_run(*args, **kwargs):
        with (root / 'encoder-attempts').open('a') as stream:
            stream.write('run\\n')
        return original_run(*args, **kwargs)
    ctx.jobs.executor.run = counted_run
    original_prepare = ctx.jobs.prepare_result
    def paused_prepare(*args, **kwargs):
        permit = original_prepare(*args, **kwargs)
        marker = root / 'publication-selected'
        if not marker.exists():
            marker.write_text(permit.permit_id)
            while True:
                time.sleep(0.1)
        return permit
    ctx.jobs.prepare_result = paused_prepare
app = create_app(cfg, context=ctx)
@app.middleware('http')
async def diagnostic_status(request, call_next):
    response = await call_next(request)
    if request.url.path == '/api/v1/jobs/execute':
        with (root / 'assignment-status').open('a') as stream:
            stream.write(str(response.status_code) + '\\n')
    return response
uvicorn.run(app, host='127.0.0.1', port=cfg.server.port, log_level='error')
"""


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class JobNode:
    def __init__(self, root: Path, name: str):
        self.root, self.name, self.port = root, name, _port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.peers: list[str] = []
        self.process = self.log = None
        self.client = httpx.Client(base_url=self.url, timeout=15, trust_env=False)

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "TAILCAM_HOST": self.name,
            "TAILCAM_PEERS": ",".join(self.peers),
            "TAILCAM_DATA_DIR": str(self.root / "data"),
            "TAILCAM_CONFIG_DIR": str(self.root / "config"),
            "TAILCAM_SYNTHETIC": "1",
            "TAILCAM_LOW_POWER": "0",
            "TEST_NODE": self.name,
            "TEST_PORT": str(self.port),
            "TEST_ROOT": str(self.root),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
        env.pop("TAILCAM_CONFIG", None)
        self.log = (self.root / "server.log").open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-c", _SERVER], env=env, stdout=self.log, stderr=self.log
        )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            assert self.process.poll() is None, (self.root / "server.log").read_text()
            try:
                response = self.client.get("/api/system", timeout=0.5)
                if response.status_code == 200:
                    self.node_id = response.json()["node_id"]
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        pytest.fail(f"Isolated {self.name} node did not become ready")

    def stop(self, *, crash=False):
        if self.process is not None:
            self.process.kill() if crash else self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None


def _wait_job(node, job_id, state, nodes, timeout=45):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = node.client.get(f"/api/v1/jobs/{job_id}")
        if response.status_code == 404:
            time.sleep(0.1)
            continue
        assert response.status_code == 200, response.text
        last = response.json()
        if last["state"] == state:
            return last
        if last["state"] in {"failed", "cancelled", "deadline_exceeded"}:
            break
        time.sleep(0.1)
    logs = {item.name: (item.root / "server.log").read_text()[-4000:] for item in nodes}
    logs.update(
        {
            item.name + " assignment": (item.root / "assignment-status").read_text()
            for item in nodes
            if (item.root / "assignment-status").exists()
        }
    )
    pytest.fail(f"Job did not reach {state}: {last}; isolated logs: {logs}")


def test_capture_storage_compute_restart_reconciles_one_committed_video(tmp_path):
    nodes = [JobNode(tmp_path / name, name) for name in ("source", "owner", "compute")]
    source, owner, compute = nodes
    for node in nodes:
        node.peers = [peer.url for peer in nodes if peer is not node]
    try:
        for node in nodes:
            node.start()
        assert source.client.get("/api/system").json()["node_roles"] == ["capture"]
        assert compute.client.get("/api/cameras?scope=local").json() == []
        assert owner.client.get("/api/cameras?scope=local").json() == []
        target = owner.client.get("/api/v1/storage/locations").json()["items"][-1]
        for node in (source, compute):
            # Only source is zero-local; processing gets explicitly bounded scratch.
            policy = node.client.get("/api/v1/storage/policy").json()["policy"]
            policy.update(
                default_destination={
                    "node_id": owner.node_id,
                    "location_id": target["location_id"],
                },
                zero_local_media=node is source,
            )
            response = node.client.patch(
                "/api/v1/storage/policy",
                json={"policy": policy, "expected_revision": policy["revision"]},
            )
            assert response.status_code == 200, response.text
        camera = source.client.get("/api/cameras?scope=local").json()[0]["id"]
        snapshot = source.client.post(f"/api/cameras/{camera}/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        image = next(
            item
            for item in owner.client.get("/api/v1/artifacts").json()["items"]
            if item["kind"] == "snapshot"
        )
        budget = {
            "wall_seconds": 90,
            "workspace_bytes": 64 * 1024**2,
            "output_bytes": 32 * 1024**2,
            "memory_bytes": 512 * 1024**2,
            "cpu_threads": 1,
        }
        spec = {
            "task": "timelapse_encode",
            "origin_node_id": source.node_id,
            "reference": {"camera_id": camera},
            "parameters": {"fps": 12},
            "resource_budget": budget,
            "input_artifacts": [
                {
                    "artifact_id": image["artifact_id"],
                    "owner_node_id": owner.node_id,
                    "sha256": image["sha256"],
                    "size_bytes": image["size_bytes"],
                    "slot": f"frame{index}",
                }
                for index in range(3)
            ],
            "outputs": [
                {"slot": "video", "kind": "timelapse_video", "mime_type": "video/mp4"},
                {"slot": "thumbnail", "kind": "thumbnail", "mime_type": "image/jpeg"},
            ],
            "placement_plan": {
                "task": "timelapse_encode",
                "requested_target": {"node_id": compute.node_id},
                "selected_target": {"node_id": compute.node_id},
                "budget": budget,
            },
        }
        response = source.client.post(
            "/api/v1/jobs", json={"spec": spec, "idempotency_key": "encode-once"}
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        remote_id = str(uuid5(UUID(job_id), "remote-stage:main"))
        _wait_job(compute, remote_id, "committing", nodes)
        assert (compute.root / "publication-selected").is_file()
        assert (compute.root / "encoder-attempts").read_text().splitlines() == ["run"]
        # The child completed real FFmpeg, and the durable output manifest is selected.
        # Losing both HTTP processes must resume publication, not rerender or recapture.
        compute.stop(crash=True)
        source.stop(crash=True)
        compute.start()
        source.start()
        completed = _wait_job(source, job_id, "succeeded", nodes)
        assert completed["stages"][0]["worker_node_id"] == compute.node_id
        assert (compute.root / "encoder-attempts").read_text().splitlines() == ["run"]
        outputs = completed["stages"][0]["outputs"]
        assert {item["slot"] for item in outputs} == {"video", "thumbnail"}
        assert all(item["owner_node_id"] == owner.node_id for item in outputs)
        for item in outputs:
            body = source.client.get(f"/api/v1/artifacts/{item['artifact_id']}/content")
            assert body.status_code == 200, body.text
            assert hashlib.sha256(body.content).hexdigest() == item["sha256"]
        catalog = owner.client.get("/api/v1/artifacts").json()["items"]
        assert len([item for item in catalog if item["kind"] == "timelapse_video"]) == 1
        assert {item["artifact_id"] for item in outputs}.issubset(
            {item["artifact_id"] for item in catalog}
        )
        assert all(item["origin_node_id"] == source.node_id for item in catalog)
        assert all(item["camera_id"] == camera for item in catalog)
        assert not list((source.root / "data").rglob("*.jpg"))
        assert not list((source.root / "data").rglob("*.mp4"))
        assert not list((compute.root / "data").rglob("*.mp4"))
        assert len({source.node_id, owner.node_id, compute.node_id}) == 3
    finally:
        for node in reversed(nodes):
            node.stop()
            node.client.close()
