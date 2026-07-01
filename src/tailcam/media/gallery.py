"""Media gallery: listing, retrieval, deletion, retention pruning."""

from __future__ import annotations

import time
from pathlib import Path

from tailcam.config import RetentionConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.models import MediaRecord
from tailcam.persistence.store import Store

log = get_logger(__name__)


class MediaGallery:
    def __init__(self, store: Store) -> None:
        self._store = store

    def list(
        self,
        camera_id: str | None = None,
        media_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MediaRecord]:
        return self._store.list_media(camera_id, media_type, limit, offset)

    def get(self, media_id: int) -> MediaRecord | None:
        return self._store.get_media(media_id)

    def delete(self, media_id: int) -> bool:
        record = self._store.get_media(media_id)
        if record is None:
            return False
        for p in (record.path, record.thumbnail):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("Could not delete %s: %s", p, exc)
        self._store.delete_media(media_id)
        return True

    def total_bytes(self) -> int:
        return self._store.total_media_bytes()

    def prune(self, retention: RetentionConfig) -> int:
        """Delete oldest media exceeding age or size limits. Returns count removed."""
        from tailcam import paths

        # If the media location itself is gone (external drive unmounted), do
        # NOT prune: unlink(missing_ok) would "succeed" without deleting the
        # files, dropping DB rows and orphaning the media when the drive returns.
        media_root = paths.media_dir()
        if not media_root.exists():
            log.warning(
                "Retention: media dir %s missing (unmounted?) — skipping prune", media_root
            )
            return 0
        cutoff = time.time() - retention.max_age_days * 86400
        max_bytes = int(retention.max_gb * 1024**3)
        # Cheap no-op check (the common case) before scanning the table.
        oldest = self._store.oldest_media_ts()
        if (oldest is None or oldest >= cutoff) and self.total_bytes() <= max_bytes:
            return 0
        removed = 0
        for record in self._store.list_media(limit=100000):
            if record.created_ts < cutoff and record.id is not None:
                if self.delete(record.id):
                    removed += 1
        current = self.total_bytes()
        if current > max_bytes:
            # Delete oldest-first until under budget. Track the running total in
            # Python instead of re-summing the whole table each iteration.
            all_media = sorted(
                self._store.list_media(limit=100000), key=lambda r: r.created_ts
            )
            for record in all_media:
                if current <= max_bytes:
                    break
                if record.id is not None and self.delete(record.id):
                    current -= record.size_bytes
                    removed += 1
        return removed
