"""Printer-focused configuration and API behavior for single-camera timelapses."""

from __future__ import annotations

import time


def _wait(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _synthetic_id(context) -> str:
    cameras = context.manager.list()
    assert cameras
    return cameras[0].descriptor.id


def test_printer_timelapse_presets_are_stable(client):
    response = client.get("/api/timelapse-presets")

    assert response.status_code == 200
    presets = {item["name"]: item["settings"] for item in response.json()}
    assert set(presets) == {"Reliable Print", "Storage Saver", "Maximum Quality"}
    assert presets["Reliable Print"]["jpeg_quality"] == 95
    assert presets["Reliable Print"]["auto_smooth"] is True
    assert presets["Storage Saver"]["interval_seconds"] == 10
    assert presets["Storage Saver"]["smooth_interpolate"] is False
    assert presets["Maximum Quality"]["jpeg_quality"] == 98
    assert presets["Maximum Quality"]["smooth_quality"] == "maximum"


def test_start_persists_and_uses_per_capture_printer_settings(
    client, context, monkeypatch
):
    from tailcam.timelapse import worker as worker_module

    qualities: list[int] = []
    original_encode = worker_module.encode_jpeg

    def recording_encode(image, quality):
        qualities.append(quality)
        return original_encode(image, quality)

    monkeypatch.setattr(worker_module, "encode_jpeg", recording_encode)
    camera_id = _synthetic_id(context)

    response = client.post(
        f"/api/cameras/{camera_id}/timelapse/start",
        json={
            "name": "Configured printer capture",
            "interval_seconds": 0.1,
            "output_fps": 24,
            "jpeg_quality": 77,
            "max_frames": 2,
            "auto_smooth": False,
            "smooth_target_fps": 48,
            "smooth_interpolate": False,
            "smooth_deflicker": False,
            "smooth_engine": "ffmpeg",
            "smooth_quality": "standard",
            "analysis_enabled": False,
            "analysis_cadence_seconds": 45,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jpeg_quality"] == 77
    assert body["max_frames"] == 2
    assert body["auto_smooth"] is False
    assert body["smooth_target_fps"] == 48
    assert body["smooth_interpolate"] is False
    assert body["smooth_deflicker"] is False
    assert body["smooth_quality"] == "standard"
    assert body["analysis_enabled"] is False
    assert body["analysis_cadence_seconds"] == 45

    tl_id = body["id"]
    assert _wait(lambda: context.timelapse.get(tl_id).state == "complete")
    saved = context.store.get_timelapse(tl_id)
    assert saved is not None
    assert saved.jpeg_quality == 77
    assert saved.max_frames == 2
    assert saved.smooth_target_fps == 48
    assert saved.analysis_cadence_seconds == 45
    assert qualities and set(qualities) == {77}


def test_start_rejects_unsafe_printer_settings(client, context):
    camera_id = _synthetic_id(context)

    for payload in (
        {"jpeg_quality": 0},
        {"jpeg_quality": 101},
        {"max_frames": -1},
        {"smooth_target_fps": 0},
        {"smooth_engine": "shell"},
        {"smooth_quality": "lossless-command"},
        {"analysis_cadence_seconds": 0},
    ):
        response = client.post(
            f"/api/cameras/{camera_id}/timelapse/start",
            json=payload,
        )
        assert response.status_code == 422, (payload, response.text)


def test_ffmpeg_output_quality_uses_fixed_safe_arguments():
    from pathlib import Path

    from tailcam.timelapse.ffmpeg import build_encode_command, build_smooth_command

    standard = build_encode_command(
        "ffmpeg",
        "/frames/*.png",
        30,
        Path("/out.mp4"),
        deflicker=False,
        quality="standard",
    )
    assert standard[standard.index("-crf") + 1] == "20"
    assert standard[standard.index("-preset") + 1] == "medium"

    maximum = build_smooth_command(
        "ffmpeg",
        Path("/frames"),
        src_fps=30,
        out_path=Path("/out.mp4"),
        target_fps=60,
        interpolate=True,
        deflicker=True,
        quality="maximum",
    )
    assert maximum[maximum.index("-crf") + 1] == "15"
    assert maximum[maximum.index("-preset") + 1] == "slower"


def test_auto_smooth_uses_persisted_capture_options(context, monkeypatch):
    camera_id = _synthetic_id(context)
    calls: list[tuple[int, dict[str, object]]] = []

    def recording_smooth(tl_id, **kwargs):
        calls.append((tl_id, kwargs))
        return context.timelapse.get(tl_id)

    monkeypatch.setattr(context.timelapse, "smooth", recording_smooth)
    record = context.timelapse.start(
        camera_id,
        interval_seconds=0.1,
        max_frames=2,
        auto_smooth=True,
        smooth_target_fps=48,
        smooth_interpolate=False,
        smooth_deflicker=False,
        smooth_engine="rife",
        smooth_quality="maximum",
    )

    assert record is not None and record.id is not None
    assert _wait(lambda: bool(calls))
    assert calls == [
        (
            record.id,
            {
                "target_fps": 48,
                "interpolate": False,
                "deflicker": False,
                "engine": "rife",
                "quality": "maximum",
            },
        )
    ]


def test_smooth_api_rejects_unknown_output_quality(client, context):
    camera_id = _synthetic_id(context)
    started = client.post(
        f"/api/cameras/{camera_id}/timelapse/start",
        json={"interval_seconds": 0.1, "max_frames": 1},
    ).json()
    assert _wait(lambda: context.timelapse.get(started["id"]).state == "complete")

    response = client.post(
        f"/api/timelapse/{started['id']}/smooth",
        json={"quality": "arbitrary-command"},
    )

    assert response.status_code == 422


def test_failed_resmooth_preserves_previous_good_artifact(context, tmp_path, monkeypatch):
    from tailcam.persistence.models import TimelapseRecord
    from tailcam.timelapse import service as service_module

    frames_dir = tmp_path / "timelapse" / "frames"
    frames_dir.mkdir(parents=True)
    (frames_dir / "000000.jpg").write_bytes(b"frame")
    previous = frames_dir.parent / "smooth.mp4"
    previous.write_bytes(b"last-good-smooth-video")
    tl_id = context.store.add_timelapse(
        TimelapseRecord(
            id=None,
            camera_id="camera",
            name="Printer",
            state="complete",
            mode="interval",
            interval_seconds=1,
            output_fps=30,
            frames_captured=1,
            created_ts=1,
            start_ts=1,
            end_ts=2,
            frames_dir=str(frames_dir),
            smooth_state="complete",
            smooth_path=str(previous),
            smooth_size_bytes=previous.stat().st_size,
        )
    )
    context.store.update_timelapse(
        tl_id,
        smooth_state="complete",
        smooth_path=str(previous),
        smooth_size_bytes=previous.stat().st_size,
    )
    monkeypatch.setattr(service_module, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(service_module, "run_ffmpeg", lambda command: False)

    context.timelapse.smooth(tl_id)

    assert _wait(lambda: context.timelapse.get(tl_id).smooth_state == "error")
    saved = context.timelapse.get(tl_id)
    assert saved.smooth_path == str(previous)
    assert saved.smooth_size_bytes == len(b"last-good-smooth-video")
    assert previous.read_bytes() == b"last-good-smooth-video"
    assert not (frames_dir.parent / "smooth.pending.mp4").exists()


def test_analysis_api_requires_ollama_and_lists_persisted_events(client, context):
    from tailcam.persistence.models import TimelapseAnalysisEventRecord

    camera_id = _synthetic_id(context)
    rejected = client.post(
        f"/api/cameras/{camera_id}/timelapse/start",
        json={"analysis_enabled": True},
    )
    assert rejected.status_code == 409
    assert "Models" in rejected.json()["detail"]

    started = client.post(
        f"/api/cameras/{camera_id}/timelapse/start",
        json={"interval_seconds": 0.1, "max_frames": 1},
    ).json()
    context.store.add_timelapse_analysis_event(
        TimelapseAnalysisEventRecord(
            id=None,
            timelapse_id=started["id"],
            frame_number=0,
            state="healthy",
            confidence=0.98,
            description="Print is attached and progressing",
            evidence_path="/frames/000000.jpg",
            created_ts=123,
        )
    )

    response = client.get(f"/api/timelapse/{started['id']}/analysis-events")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "timelapse_id": started["id"],
            "frame_number": 0,
            "state": "healthy",
            "confidence": 0.98,
            "description": "Print is attached and progressing",
            "created_ts": 123.0,
        }
    ]
    summary = client.get(f"/api/timelapse/{started['id']}").json()
    assert summary["analysis_event_count"] == 1
    assert summary["analysis_latest_state"] == "healthy"
