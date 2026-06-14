"""Timelapse capture → encode → list/get/delete, end to end with the synthetic
camera, plus the REST endpoints."""

from __future__ import annotations

import time

import pytest

from tailcam.timelapse.service import TimelapseService


def _wait(predicate, timeout=10.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def service(context):
    # Fast capture so the test doesn't dawdle.
    cfg = context.config.timelapse
    cfg.default_interval_seconds = 0.1
    cfg.default_output_fps = 10
    return context.timelapse


def _synthetic_id(context) -> str:
    cams = context.manager.list()
    assert cams, "synthetic camera should be present"
    return cams[0].descriptor.id


def test_capture_encode_lifecycle(service, context):
    cam_id = _synthetic_id(context)
    record = service.start(cam_id, interval_seconds=0.1, output_fps=10)
    assert record is not None and record.id is not None
    tl_id = record.id
    assert record.state == "capturing"

    # Frames should start accumulating.
    assert _wait(lambda: service.get(tl_id).frames_captured >= 3), "no frames captured"

    final = service.stop(tl_id)
    assert final is not None
    # Encoding runs on a background thread; wait for completion.
    assert _wait(lambda: service.get(tl_id).state == "complete"), "encode did not complete"

    done = service.get(tl_id)
    assert done.video_path and done.video_path.endswith(".mp4")
    from pathlib import Path

    assert Path(done.video_path).exists()
    assert done.size_bytes > 0
    assert done.frames_captured >= 3
    assert done.width > 0 and done.height > 0
    assert done.thumb_path and Path(done.thumb_path).exists()
    # Raw frames are retained for future post-processing.
    assert list(Path(done.frames_dir).glob("*.jpg"))


def test_max_frames_auto_finalizes(context):
    context.config.timelapse.max_frames = 4
    svc = TimelapseService(context.manager, context.store, context.config.timelapse)
    cam_id = _synthetic_id(context)
    record = svc.start(cam_id, interval_seconds=0.1, output_fps=10)
    assert record is not None
    tl_id = record.id
    # Hitting the frame cap should finalize the capture without an explicit stop.
    assert _wait(lambda: svc.get(tl_id).state == "complete"), "did not auto-finalize"
    assert svc.get(tl_id).frames_captured == 4


def test_delete_removes_files_and_row(service, context):
    from pathlib import Path

    cam_id = _synthetic_id(context)
    record = service.start(cam_id, interval_seconds=0.1)
    tl_id = record.id
    assert _wait(lambda: service.get(tl_id).frames_captured >= 2)
    service.stop(tl_id)
    assert _wait(lambda: service.get(tl_id).state == "complete")
    job_dir = Path(record.frames_dir).parent
    assert job_dir.exists()

    assert service.delete(tl_id) is True
    assert not job_dir.exists()
    assert service.get(tl_id) is None


def test_interrupt_marks_state(service, context):
    cam_id = _synthetic_id(context)
    record = service.start(cam_id, interval_seconds=0.1)
    assert _wait(lambda: service.get(record.id).frames_captured >= 2)
    # Simulate a process restart: stop workers, then run the startup sweep.
    service.shutdown()
    n = context.store.interrupt_active_timelapses()
    assert n >= 1
    assert context.store.get_timelapse(record.id).state == "interrupted"
    # The interrupted capture can still be encoded from its frames.
    service.encode(record.id)
    assert _wait(lambda: service.get(record.id).state == "complete")


# -- REST endpoints --------------------------------------------------------


def test_timelapse_api(client, context):
    context.config.timelapse.default_interval_seconds = 0.1
    cam_id = _synthetic_id(context)

    r = client.post(f"/api/cameras/{cam_id}/timelapse/start", json={"interval_seconds": 0.1})
    assert r.status_code == 200, r.text
    tl = r.json()
    tl_id = tl["id"]
    assert tl["state"] == "capturing"

    assert _wait(lambda: client.get(f"/api/timelapse/{tl_id}").json()["frames_captured"] >= 2)

    listed = client.get("/api/timelapse").json()
    assert any(t["id"] == tl_id for t in listed)

    assert client.post(f"/api/timelapse/{tl_id}/stop").status_code == 200
    assert _wait(lambda: client.get(f"/api/timelapse/{tl_id}").json()["state"] == "complete")

    info = client.get(f"/api/timelapse/{tl_id}").json()
    assert info["has_video"] and info["has_thumb"]
    assert client.get(f"/timelapse/{tl_id}/file").status_code == 200
    assert client.get(f"/timelapse/{tl_id}/thumbnail").status_code == 200

    assert client.delete(f"/api/timelapse/{tl_id}").status_code == 200
    assert client.get(f"/api/timelapse/{tl_id}").status_code == 404
