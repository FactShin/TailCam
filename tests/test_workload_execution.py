"""Real isolated worker processes, safe inputs and fenced output publication."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from tailcam.config import AppConfig
from tailcam.jobs.models import ArtifactRef, JobSpec, OutputSlot, ResourceBudget
from tailcam.jobs.service import JobService
from tailcam.storage.service import StorageService
from tailcam.workloads.executor import ProcessExecutor, validate_budget
from tailcam.workloads.handlers import extract_archive
from tailcam.workloads.process import ExecutionError, ProcessRunner, child_environment


def test_child_environment_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("TAILCAM_PEERS", "http://example.invalid")
    monkeypatch.setenv("HF_TOKEN", "not-a-real-test-token")
    before = dict(os.environ)
    env = child_environment(tmp_path)
    assert "TAILCAM_PEERS" not in env and "HF_TOKEN" not in env
    for key in ("HOME", "HF_HOME", "TORCH_HOME", "YOLO_CONFIG_DIR", "TMPDIR"):
        assert Path(env[key]).is_relative_to(tmp_path)
    assert env["HF_HUB_OFFLINE"] == "1"
    assert dict(os.environ) == before


def test_real_child_encodes_artifact_frames(tmp_path):
    from tailcam.timelapse.ffmpeg import passive_ffmpeg_present

    if not passive_ffmpeg_present():
        pytest.skip("FFmpeg is not installed")
    frames = []
    for index in range(3):
        path = tmp_path / f"input-{index}.jpg"
        assert cv2.imwrite(str(path), np.full((32, 48, 3), index * 70, dtype=np.uint8))
        frames.append({"slot": f"frame_{index:06d}", "path": path.name})
    manifest = ProcessRunner().run(
        {
            "task": "timelapse_encode",
            "parameters": {"fps": 3},
            "inputs": frames,
            "workspace_bytes": 8 * 1024**2,
            "output_bytes": 4 * 1024**2,
            "output_slots": ["video", "thumbnail"],
        },
        tmp_path,
        deadline=time.time() + 20,
    )
    assert manifest["result"] == {"width": 48, "height": 32, "frames": 3, "device": "cpu"}
    assert {output["slot"] for output in manifest["outputs"]} == {"video", "thumbnail"}
    for output in manifest["outputs"]:
        data = (tmp_path / output["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == output["sha256"]
    capture = cv2.VideoCapture(str(tmp_path / "timelapse.mp4"))
    try:
        assert capture.isOpened() and capture.read()[0]
    finally:
        capture.release()


@pytest.mark.parametrize("cancelled", [False, True])
def test_deadline_and_cancellation_terminate_owned_descendants(tmp_path, monkeypatch, cancelled):
    # Replace only the fixed bootstrap at the process-launch boundary with a
    # deliberately blocked tree. Neither process opens cameras or services.
    original = subprocess.Popen
    child_code = (
        "import pathlib,signal,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "p=pathlib.Path('heartbeat')\n"
        "while True:\n p.write_text(str(time.time()))\n time.sleep(.03)\n"
    )
    script = (
        "import pathlib,signal,subprocess,sys,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sys.stdin.buffer.readline()\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
        "pathlib.Path('descendant.json').write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )

    def launch(_command, **kwargs):
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("tailcam.workloads.process.subprocess.Popen", launch)
    stop = threading.Event()
    timer = threading.Timer(0.7, stop.set) if cancelled else None
    if timer:
        timer.start()
    started = time.monotonic()
    with pytest.raises(ExecutionError) as error:
        ProcessRunner().run(
            {}, tmp_path, deadline=time.time() + (8 if cancelled else 1), cancel=stop
        )
    if timer:
        timer.cancel()
    assert error.value.code == ("cancelled" if cancelled else "deadline_exceeded")
    assert time.monotonic() - started < 4
    heartbeat = tmp_path / "heartbeat"
    assert heartbeat.exists()
    value = heartbeat.read_text()
    time.sleep(0.2)
    assert heartbeat.read_text() == value


@pytest.mark.parametrize("name", ["../escape", "/escape", "a/../../escape", "A/CON.pt", "a:stream"])
def test_archive_validation_precedes_writes(tmp_path, name):
    path = tmp_path / "model.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("safe.bin", b"okay")
        archive.writestr(name, b"bad")
    with pytest.raises(ValueError):
        extract_archive(path, tmp_path / "expanded", 1024)
    assert not (tmp_path / "expanded").exists()


def test_hard_resource_claims_fail_closed():
    from tailcam.jobs.models import JobError

    with pytest.raises(JobError, match="hard memory"):
        validate_budget(ResourceBudget(require_hard_memory_limit=True))


def test_fenced_encoder_publishes_only_after_prepare(store, tmp_path, monkeypatch):
    from tailcam.timelapse.ffmpeg import passive_ffmpeg_present

    if not passive_ffmpeg_present():
        pytest.skip("FFmpeg is not installed")
    config = AppConfig()
    storage = StorageService(config, store, store.get_node_id())
    storage.register_location(str(tmp_path / "canonical"), create=True, make_default=True)
    executor = ProcessExecutor(storage, node_id=storage.node_id)
    jobs = JobService(config, store, storage.node_id, storage, executor=executor)
    ok, data = cv2.imencode(".jpg", np.full((32, 48, 3), 120, dtype=np.uint8))
    assert ok
    frame = storage.put_bytes("timelapse_frame", data.tobytes(), mime_type="image/jpeg")
    record = jobs.submit(
        JobSpec(
            task="timelapse_encode",
            origin_node_id=storage.node_id,
            input_artifacts=[ArtifactRef.from_artifact(frame, "frame_000000")],
            outputs=[
                OutputSlot(slot="video", kind="timelapse_video", mime_type="video/mp4"),
                OutputSlot(slot="thumbnail", kind="thumbnail", mime_type="image/jpeg"),
            ],
        ),
        idempotency_key="encode",
    )
    lease = jobs.claim_local()
    assert lease is not None
    order = []
    prepare = jobs.prepare_result
    finalize = storage.finalize

    def preparing(*args, **kwargs):
        order.append("prepare")
        return prepare(*args, **kwargs)

    def publishing(*args, **kwargs):
        assert order and order[0] == "prepare"
        order.append("publish")
        return finalize(*args, **kwargs)

    monkeypatch.setattr(jobs, "prepare_result", preparing)
    monkeypatch.setattr(storage, "finalize", publishing)
    executor.execute(lease, jobs)
    finished = jobs.get(record.job_id)
    assert finished is not None and finished.state == "succeeded", finished
    assert len(finished.stages[0].outputs) == 2
    assert order == ["prepare", "publish", "publish"]
    assert (
        storage.catalog.connection.execute(
            "SELECT COUNT(*) FROM storage_reservations WHERE category='workspace'"
        ).fetchone()[0]
        == 0
    )


def test_tighter_cpu_allowance_is_rejected_before_process_start():
    from tailcam.jobs.models import JobError

    with pytest.raises(JobError) as exc:
        validate_budget(ResourceBudget(cpu_threads=2, cpu_seconds=10, wall_seconds=10))
    assert exc.value.code == "unsupported_cpu_budget"
    validate_budget(ResourceBudget(cpu_threads=2, cpu_seconds=20, wall_seconds=10))
