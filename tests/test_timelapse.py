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


# -- smoothing (ffmpeg post-processing) ------------------------------------


def test_build_smooth_command_interpolates_and_deflickers():
    from pathlib import Path

    from tailcam.timelapse.ffmpeg import build_smooth_command

    cmd = build_smooth_command(
        "ffmpeg", Path("/frames"), src_fps=30, out_path=Path("/out.mp4"),
        target_fps=60, interpolate=True, deflicker=True,
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "deflicker" in vf
    assert "minterpolate=fps=60" in vf
    assert "libx264" in cmd
    assert "30" in cmd  # source framerate
    assert "/frames/%06d.jpg" in cmd


def test_build_smooth_command_no_interpolate_uses_fps():
    from pathlib import Path

    from tailcam.timelapse.ffmpeg import build_smooth_command

    cmd = build_smooth_command(
        "ffmpeg", Path("/f"), 30, Path("/o.mp4"), 48, interpolate=False, deflicker=False,
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "minterpolate" not in vf
    assert "fps=48" in vf


def test_ffmpeg_locator_prefers_system(monkeypatch):
    from tailcam.timelapse import ffmpeg as ff

    monkeypatch.setattr(ff.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    assert ff.ffmpeg_path() == "/usr/bin/ffmpeg"
    assert ff.ffmpeg_source() == "system"
    assert ff.ffmpeg_available() is True


def test_ffmpeg_locator_falls_back_to_bundled(monkeypatch):
    from tailcam.timelapse import ffmpeg as ff

    monkeypatch.setattr(ff.shutil, "which", lambda _: None)
    monkeypatch.setattr(ff.sys, "platform", "linux")  # no known-binary candidates
    path = ff.ffmpeg_path()
    assert path is not None  # imageio-ffmpeg bundles a static binary
    assert ff.ffmpeg_source() == "bundled"


def test_smooth_end_to_end(service, context):
    from pathlib import Path

    from tailcam.timelapse.ffmpeg import ffmpeg_available

    assert ffmpeg_available(), "imageio-ffmpeg should provide a binary"
    cam_id = _synthetic_id(context)
    record = service.start(cam_id, interval_seconds=0.1, output_fps=8)
    tl_id = record.id
    assert _wait(lambda: service.get(tl_id).frames_captured >= 4)
    service.stop(tl_id)
    assert _wait(lambda: service.get(tl_id).state == "complete")

    service.smooth(tl_id, target_fps=24)
    assert _wait(lambda: service.get(tl_id).smooth_state == "complete", timeout=40), "smooth failed"
    done = service.get(tl_id)
    assert done.smooth_path and Path(done.smooth_path).exists()
    assert done.smooth_size_bytes > 0


def test_smooth_api(client, context):
    from tailcam.timelapse.ffmpeg import ffmpeg_available

    assert ffmpeg_available()
    cam_id = _synthetic_id(context)
    r = client.post(f"/api/cameras/{cam_id}/timelapse/start", json={"interval_seconds": 0.1})
    tl_id = r.json()["id"]
    assert _wait(lambda: client.get(f"/api/timelapse/{tl_id}").json()["frames_captured"] >= 4)
    client.post(f"/api/timelapse/{tl_id}/stop")
    assert _wait(lambda: client.get(f"/api/timelapse/{tl_id}").json()["state"] == "complete")

    pp = client.get("/api/postprocess").json()
    assert pp["available"] is True

    assert client.post(f"/api/timelapse/{tl_id}/smooth", json={"target_fps": 24}).status_code == 200
    assert _wait(
        lambda: client.get(f"/api/timelapse/{tl_id}").json()["smooth_state"] == "complete",
        timeout=40,
    )
    info = client.get(f"/api/timelapse/{tl_id}").json()
    assert info["has_smooth"]
    assert info["smooth_engine"] == "ffmpeg"
    assert client.get(f"/timelapse/{tl_id}/smooth").status_code == 200


# -- RIFE engine -----------------------------------------------------------


def test_rife_locator(monkeypatch, tmp_path):
    from tailcam.timelapse import rife

    monkeypatch.delenv("TAILCAM_RIFE", raising=False)
    monkeypatch.setattr(rife.shutil, "which", lambda _: None)
    monkeypatch.setattr(rife.sys, "platform", "linux")
    monkeypatch.setattr(rife.Path, "home", classmethod(lambda cls: tmp_path))
    assert rife.rife_path() is None
    assert rife.rife_available() is False

    # explicit env override wins
    fake = tmp_path / "rife-ncnn-vulkan"
    fake.write_text("")
    monkeypatch.setenv("TAILCAM_RIFE", str(fake))
    assert rife.rife_path() == str(fake)
    assert rife.rife_available() is True


def test_build_rife_command():
    from pathlib import Path

    from tailcam.timelapse.rife import build_rife_command

    cmd = build_rife_command("/bin/rife", Path("/in"), Path("/out"), 64, model="rife-v4.6")
    assert cmd[cmd.index("-i") + 1] == "/in"
    assert cmd[cmd.index("-o") + 1] == "/out"
    assert cmd[cmd.index("-n") + 1] == "64"
    assert cmd[cmd.index("-m") + 1] == "rife-v4.6"


def test_build_encode_command_globs_pngs():
    from pathlib import Path

    from tailcam.timelapse.ffmpeg import build_encode_command

    cmd = build_encode_command("ffmpeg", "/interp/*.png", 32, Path("/o.mp4"), deflicker=True)
    assert "glob" in cmd
    assert "/interp/*.png" in cmd
    assert "deflicker=mode=pm:size=10" in cmd
    assert "minterpolate" not in " ".join(cmd)


def _capture_complete(service, context):
    cam_id = _synthetic_id(context)
    rec = service.start(cam_id, interval_seconds=0.1, output_fps=8)
    assert _wait(lambda: service.get(rec.id).frames_captured >= 4)
    service.stop(rec.id)
    assert _wait(lambda: service.get(rec.id).state == "complete")
    return rec.id


def test_smooth_rife_pipeline(service, context, monkeypatch):
    from pathlib import Path

    import cv2
    import numpy as np

    from tailcam.timelapse import service as svc_mod

    tl_id = _capture_complete(service, context)

    monkeypatch.setattr(svc_mod, "rife_available", lambda *_: True)
    monkeypatch.setattr(svc_mod, "rife_path", lambda *_: "/usr/bin/rife-ncnn-vulkan")

    def fake_run_rife(cmd, cwd=None, timeout=3600.0):
        out_dir = Path(cmd[cmd.index("-o") + 1])
        n = int(cmd[cmd.index("-n") + 1])
        for i in range(min(n, 16)):
            img = (np.random.rand(120, 160, 3) * 255).astype("uint8")
            cv2.imwrite(str(out_dir / f"{i:08d}.png"), img)
        return True

    monkeypatch.setattr(svc_mod, "run_rife", fake_run_rife)

    service.smooth(tl_id, engine="rife", target_fps=24)
    assert _wait(lambda: service.get(tl_id).smooth_state == "complete", timeout=40)
    done = service.get(tl_id)
    assert done.smooth_engine == "rife"
    assert done.smooth_path and Path(done.smooth_path).exists()


def test_smooth_rife_unavailable_falls_back(service, context, monkeypatch):
    from tailcam.timelapse import service as svc_mod

    tl_id = _capture_complete(service, context)
    monkeypatch.setattr(svc_mod, "rife_available", lambda *_: False)
    service.smooth(tl_id, engine="rife")
    assert _wait(lambda: service.get(tl_id).smooth_state == "complete", timeout=40)
    assert service.get(tl_id).smooth_engine == "ffmpeg"


def test_smooth_rife_run_failure_falls_back(service, context, monkeypatch):
    from tailcam.timelapse import service as svc_mod

    tl_id = _capture_complete(service, context)
    monkeypatch.setattr(svc_mod, "rife_available", lambda *_: True)
    monkeypatch.setattr(svc_mod, "rife_path", lambda *_: "/usr/bin/rife-ncnn-vulkan")
    monkeypatch.setattr(svc_mod, "run_rife", lambda *a, **k: False)  # RIFE crashes
    service.smooth(tl_id, engine="rife")
    assert _wait(lambda: service.get(tl_id).smooth_state == "complete", timeout=40)
    assert service.get(tl_id).smooth_engine == "ffmpeg"


def test_postprocess_engines_and_default(client, context):
    pp = client.get("/api/postprocess").json()
    ids = {e["id"] for e in pp["engines"]}
    assert ids == {"ffmpeg", "rife"}
    assert next(e for e in pp["engines"] if e["id"] == "ffmpeg")["available"] is True
    assert pp["default_engine"] == "ffmpeg"

    # switch the default engine; persists to config
    r = client.post("/api/postprocess", json={"default_engine": "rife"})
    assert r.status_code == 200
    assert r.json()["default_engine"] == "rife"
    assert context.config.timelapse.smooth_engine == "rife"
    assert client.post("/api/postprocess", json={"default_engine": "bogus"}).status_code == 400
