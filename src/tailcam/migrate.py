"""One-time migration of a pre-rename *AnyCam* install into the TailCam layout.

When a machine that previously ran AnyCam installs TailCam (a clean
uninstall/reinstall — there is no longer an ``anycam`` command or import shim),
this moves the old config, media, and SQLite database into the TailCam
directories so cameras, settings, recordings, and motion-event history all
carry over. It is **idempotent** and **safe**: it only moves data into a
TailCam location that doesn't already hold its own, and it never touches a live
TailCam install.

It runs automatically (once) from the CLI's top-level callback, and can be
invoked explicitly with ``tailcam migrate``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from tailcam import paths
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

# When the user explicitly points TailCam at a directory, that location is
# authoritative — we must not drag data from the default AnyCam locations into
# it. This also keeps the test suite (which isolates via these vars) from
# touching a developer's real install.
_OVERRIDE_VARS = ("TAILCAM_CONFIG_DIR", "TAILCAM_CONFIG", "TAILCAM_DATA_DIR")


def _paths_overridden() -> bool:
    return any(os.environ.get(v) for v in _OVERRIDE_VARS)

# Markers that prove a directory actually holds AnyCam data — not, say, the
# bare ``~/.local/share/tailcam/`` the installer creates for the venv.
_CONFIG_MARKERS = ("config.toml", "config.toml.bad")
_DATA_MARKERS = ("anycam.db", "tailcam.db", "media")

# Never carried across: the old venv lived under the Linux data dir, and pid
# files are stale the moment the old process exits.
_SKIP_NAMES = {"venv"}
_SKIP_SUFFIXES = {".pid"}


def _has_data(directory: Path, markers: tuple[str, ...]) -> bool:
    return directory.is_dir() and any((directory / m).exists() for m in markers)


def _targets() -> list[tuple[Path, Path, tuple[str, ...]]]:
    """(legacy, new, markers) pairs, de-duplicated.

    On macOS the config and data dirs are the same path, so the pair would
    otherwise appear twice.
    """
    pairs = [
        (paths.legacy_config_dir(), paths.config_dir(), _CONFIG_MARKERS),
        (paths.legacy_data_dir(), paths.data_dir(), _DATA_MARKERS),
    ]
    seen: set[tuple[Path, Path]] = set()
    out: list[tuple[Path, Path, tuple[str, ...]]] = []
    for legacy, new, markers in pairs:
        key = (legacy.resolve(), new.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append((legacy, new, markers))
    return out


def needs_migration() -> bool:
    """True if a populated AnyCam dir exists and its TailCam counterpart is empty."""
    if _paths_overridden():
        return False
    return any(
        _has_data(legacy, markers) and not _has_data(new, markers)
        for legacy, new, markers in _targets()
    )


def _skip(item: Path) -> bool:
    return item.name in _SKIP_NAMES or item.suffix in _SKIP_SUFFIXES


def migrate() -> list[str]:
    """Move AnyCam data into the TailCam layout. Returns a human-readable log."""
    actions: list[str] = []
    for legacy, new, markers in _targets():
        if not _has_data(legacy, markers):
            continue
        if _has_data(new, markers):
            actions.append(f"Skipped {new} (already has TailCam data).")
            continue
        new.mkdir(parents=True, exist_ok=True)
        for item in sorted(legacy.iterdir()):
            if _skip(item):
                continue
            dest = new / item.name
            if dest.exists():
                continue
            shutil.move(str(item), str(dest))
            actions.append(f"Moved {item.name} → {new}")
        # Tidy up the now-empty legacy dir (ignore if anything's left behind).
        try:
            legacy.rmdir()
        except OSError:
            pass

    actions += _rename_database()
    if actions:
        log.info("Migrated AnyCam data to TailCam: %s", "; ".join(actions))
    return actions


def _rename_database() -> list[str]:
    """anycam.db → tailcam.db (plus its -wal/-shm sidecars), in the data dir."""
    actions: list[str] = []
    data = paths.data_dir()
    for suffix in ("", "-wal", "-shm"):
        old = data / f"anycam.db{suffix}"
        new = data / f"tailcam.db{suffix}"
        if old.exists() and not new.exists():
            old.rename(new)
            actions.append(f"Renamed {old.name} → {new.name}")
    return actions
