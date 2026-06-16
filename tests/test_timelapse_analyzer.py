"""Printer-health analysis is strict, durable, and never blocks capture."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event


def _wait(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _timelapse_id(store, tmp_path: Path) -> int:
    from tailcam.persistence.models import TimelapseRecord

    record = TimelapseRecord(
        id=None,
        camera_id="camera",
        name="Printer",
        state="capturing",
        mode="interval",
        interval_seconds=1,
        output_fps=30,
        frames_captured=0,
        created_ts=1,
        start_ts=1,
        end_ts=None,
        frames_dir=str(tmp_path),
    )
    return store.add_timelapse(record)


def test_printer_analysis_parser_is_strict_and_clamps_confidence():
    from tailcam.timelapse.analyzer import coerce_printer_analysis

    result = coerce_printer_analysis(
        {"state": "failure", "confidence": 9, "description": "Detached print"}
    )

    assert result is not None
    assert result.state == "failure"
    assert result.confidence == 1.0
    assert result.description == "Detached print"
    assert coerce_printer_analysis({"state": "looks_bad", "confidence": 1}) is None
    assert coerce_printer_analysis({"state": "healthy", "confidence": "bad"}) is not None
    # A model that returns valid JSON but not an object must not crash on .get.
    assert coerce_printer_analysis("healthy") is None
    assert coerce_printer_analysis([{"state": "failure"}]) is None


def test_analysis_queue_returns_immediately_and_persists_evidence(store, tmp_path):
    from tailcam.timelapse.analyzer import PrinterAnalysis, TimelapseAnalysisQueue

    tl_id = _timelapse_id(store, tmp_path)
    evidence = tmp_path / "000004.jpg"
    evidence.write_bytes(b"frame")

    class SlowAnalyzer:
        def analyze_path(self, path):
            time.sleep(0.2)
            return PrinterAnalysis("possible_failure", 0.72, "Possible spaghetti")

    queue = TimelapseAnalysisQueue(store, SlowAnalyzer())
    started = time.monotonic()
    queue.submit(tl_id, 4, evidence)
    elapsed = time.monotonic() - started

    assert elapsed < 0.05
    assert _wait(lambda: len(store.list_timelapse_analysis_events(tl_id)) == 1)
    event = store.list_timelapse_analysis_events(tl_id)[0]
    assert event.state == "possible_failure"
    assert event.frame_number == 4
    assert event.evidence_path == str(evidence)
    queue.shutdown()


def test_analysis_queue_records_uncertain_when_model_fails(store, tmp_path):
    from tailcam.timelapse.analyzer import TimelapseAnalysisQueue

    tl_id = _timelapse_id(store, tmp_path)
    evidence = tmp_path / "000001.jpg"
    evidence.write_bytes(b"frame")

    class OfflineAnalyzer:
        def analyze_path(self, path):
            return None

    queue = TimelapseAnalysisQueue(store, OfflineAnalyzer())
    queue.submit(tl_id, 1, evidence)

    assert _wait(lambda: len(store.list_timelapse_analysis_events(tl_id)) == 1)
    event = store.list_timelapse_analysis_events(tl_id)[0]
    assert event.state == "uncertain"
    assert "unavailable" in event.description.lower()
    queue.shutdown()


def test_analysis_queue_coalesces_stale_pending_frames_per_timelapse(store, tmp_path):
    from tailcam.timelapse.analyzer import PrinterAnalysis, TimelapseAnalysisQueue

    tl_id = _timelapse_id(store, tmp_path)
    entered = Event()
    release = Event()
    analyzed: list[str] = []

    class BlockingAnalyzer:
        def analyze_path(self, path):
            analyzed.append(path.name)
            if len(analyzed) == 1:
                entered.set()
                release.wait(timeout=2)
            return PrinterAnalysis("healthy", 0.9, "Print progressing")

    queue = TimelapseAnalysisQueue(store, BlockingAnalyzer())
    queue.submit(tl_id, 0, tmp_path / "000000.jpg")
    assert entered.wait(timeout=2)
    for frame_number in (1, 2, 3):
        queue.submit(tl_id, frame_number, tmp_path / f"{frame_number:06d}.jpg")
    release.set()
    queue.shutdown()

    assert analyzed == ["000000.jpg", "000003.jpg"]


def test_analysis_queue_survives_deletion_during_analysis(store, tmp_path):
    from tailcam.timelapse.analyzer import PrinterAnalysis, TimelapseAnalysisQueue

    first_id = _timelapse_id(store, tmp_path)
    second_id = _timelapse_id(store, tmp_path)
    entered = Event()
    release = Event()

    class BlockingAnalyzer:
        def analyze_path(self, path):
            if not entered.is_set():
                entered.set()
                release.wait(timeout=2)
            return PrinterAnalysis("healthy", 0.9, "Print progressing")

    queue = TimelapseAnalysisQueue(store, BlockingAnalyzer())
    queue.submit(first_id, 0, tmp_path / "first.jpg")
    assert entered.wait(timeout=2)
    store.delete_timelapse(first_id)
    queue.submit(second_id, 0, tmp_path / "second.jpg")
    release.set()

    assert _wait(lambda: len(store.list_timelapse_analysis_events(second_id)) == 1)
    queue.shutdown()


def test_capture_schedules_analysis_at_configured_cadence(context):
    from tailcam.timelapse.service import TimelapseService

    submitted: list[tuple[int, int, Path]] = []

    class RecordingQueue:
        def submit(self, tl_id, frame_number, evidence_path):
            submitted.append((tl_id, frame_number, evidence_path))

        def shutdown(self):
            return None

    config = context.config.timelapse
    service = TimelapseService(
        context.manager,
        context.store,
        config,
        analysis_queue=RecordingQueue(),
    )
    camera_id = context.manager.list()[0].descriptor.id

    record = service.start(
        camera_id,
        interval_seconds=0.1,
        max_frames=4,
        analysis_enabled=True,
        analysis_cadence_seconds=0.2,
    )

    assert record is not None and record.id is not None
    assert _wait(lambda: service.get(record.id).state == "complete")
    assert [frame_number for _, frame_number, _ in submitted] == [0, 1, 3]
    service.shutdown()
