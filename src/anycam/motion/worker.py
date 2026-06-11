"""Per-camera motion worker: samples the frame buffer, detects motion with
hysteresis/cooldown, logs events, and optionally triggers auto-recording.
"""

from __future__ import annotations

import threading
import time

from anycam.config import MotionConfig
from anycam.logging_setup import get_logger
from anycam.media.recorder import RecordingService
from anycam.motion.detector import MotionDetector
from anycam.motion.events import EventLog

log = get_logger(__name__)


class MotionWorker:
    def __init__(
        self,
        camera_id: str,
        buffer,
        config: MotionConfig,
        event_log: EventLog,
        recorder: RecordingService | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.buffer = buffer
        self.config = config
        self._event_log = event_log
        self._recorder = recorder
        self._detector = MotionDetector(config.sensitivity, config.min_area)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"motion-{camera_id}", daemon=True
        )
        # Latest boxes for the UI overlay (read by stream/API threads).
        self.boxes: list[tuple[int, int, int, int]] = []
        self.active = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        interval = 1.0 / max(1, self.config.sample_fps)
        last_seq = -1
        event_id: int | None = None
        peak_score = 0.0
        last_motion_ts = 0.0
        recording_triggered = False

        try:
            while not self._stop.is_set():
                frame = self.buffer.await_latest(last_seq, timeout=1.0)
                if frame is None:
                    continue
                last_seq = frame.seq
                result = self._detector.process(frame.image)
                self.boxes = result.boxes
                now = time.time()

                if result.motion:
                    last_motion_ts = now
                    peak_score = max(peak_score, result.score)
                    if not self.active:
                        self.active = True
                        event_id = self._event_log.open_event(self.camera_id, now, result.score)
                        if self.config.auto_record and self._recorder:
                            recording_triggered = self._recorder.start(
                                self.camera_id, trigger="motion"
                            )
                elif self.active and (now - last_motion_ts) > self.config.cooldown_seconds:
                    self.active = False
                    recording_id = None
                    if recording_triggered and self._recorder:
                        # Let the tail finish, then close the recording.
                        time.sleep(self.config.record_tail_seconds)
                        record = self._recorder.stop(self.camera_id)
                        recording_id = record.id if record else None
                        recording_triggered = False
                    if event_id is not None:
                        self._event_log.close_event(event_id, now, peak_score, recording_id)
                    event_id = None
                    peak_score = 0.0

                time.sleep(interval)
        finally:
            # The worker is stopping (motion toggled off / app shutdown). Close
            # any open event and recording, or they'd stay "ongoing" forever.
            self.active = False
            if event_id is not None:
                recording_id = None
                if recording_triggered and self._recorder:
                    record = self._recorder.stop(self.camera_id)
                    recording_id = record.id if record else None
                end_ts = last_motion_ts or time.time()
                self._event_log.close_event(event_id, end_ts, peak_score, recording_id)
