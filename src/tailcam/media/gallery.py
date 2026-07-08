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
        self._delete_record(record)
        return True

    def _delete_record(self, record: MediaRecord) -> None:
        """Unlink a record's files + drop its row. Takes the already-loaded
        record so callers with one in hand (prune) skip a redundant get_media."""
        for p in (record.path, record.thumbnail):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("Could not delete %s: %s", p, exc)
        if record.id is not None:
            self._store.delete_media(record.id)

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
        # Load the media table once (oldest-first) and reuse it for both the
        # age and size passes, deleting via the already-loaded record so we
        # don't re-fetch each row.
        removed = 0
        media = sorted(self._store.list_media(limit=100000), key=lambda r: r.created_ts)
        survivors: list[MediaRecord] = []
        for record in media:
            if record.created_ts < cutoff:
                self._delete_record(record)
                removed += 1
            else:
                survivors.append(record)
        current = self.total_bytes()
        if current > max_bytes:
            # Delete oldest-first until under budget, tracking the running total
            # in Python instead of re-summing the table each iteration.
            for record in survivors:
                if current <= max_bytes:
                    break
                self._delete_record(record)
                current -= record.size_bytes
                removed += 1
        return removed
