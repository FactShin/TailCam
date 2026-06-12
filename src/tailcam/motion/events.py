"""Motion event log backed by SQLite."""

from __future__ import annotations

from tailcam.persistence.models import MotionEventRecord
from tailcam.persistence.store import Store


class EventLog:
    def __init__(self, store: Store) -> None:
        self._store = store

    def open_event(self, camera_id: str, start_ts: float, peak_score: float) -> int:
        return self._store.add_motion_event(
            MotionEventRecord(
                id=None,
                camera_id=camera_id,
                start_ts=start_ts,
                end_ts=None,
                peak_score=peak_score,
                recording_id=None,
            )
        )

    def close_event(
        self, event_id: int, end_ts: float, peak_score: float, recording_id: int | None
    ) -> None:
        self._store.update_motion_event(event_id, end_ts, peak_score, recording_id)

    def set_thumb(self, event_id: int, thumb_path: str) -> None:
        self._store.set_event_thumb(event_id, thumb_path)

    def set_analysis(
        self, event_id: int, label: str, description: str | None, confidence: float | None
    ) -> None:
        self._store.set_event_analysis(event_id, label, description, confidence)

    def list(self, camera_id: str | None = None, limit: int = 50, offset: int = 0):
        return self._store.list_motion_events(camera_id, limit, offset)
