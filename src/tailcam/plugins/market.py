"""The plugin marketplace: browse a curated registry, install with verification.

The registry is a static ``index.json`` (by default served from the TailCam
repository's ``marketplace/`` folder — community plugins land there via pull
request and review). Each entry pins the plugin file's **sha256**; installing
means: download over HTTPS → verify size and checksum → syntax-check → write
atomically into the drop-in folder with a metadata sidecar. Anything that
doesn't match the registry never touches disk.

Security model (also in docs/plugins): plugins run with the app's full
privileges — there is no sandbox. The defenses are *curation* (a reviewed,
checksum-pinned registry), *verification* (the checks above), and *explicit
user action* (nothing installs or runs without a click). A private/self-hosted
registry can be used by pointing ``plugins.registry_url`` elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from tailcam.config import PluginsConfig
from tailcam.logging_setup import get_logger
from tailcam.plugins.registry import dropin_dir

log = get_logger(__name__)

_INDEX_TIMEOUT = 15.0
_DOWNLOAD_TIMEOUT = 30.0
_CACHE_TTL = 600.0  # re-fetch the index at most every 10 minutes
_MAX_PLUGIN_BYTES = 1_000_000  # a single-file plugin has no business being bigger
_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_]*\.py$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_META_SUFFIX = ".meta.json"


class MarketError(Exception):
    """User-presentable marketplace failure."""


@dataclass
class MarketPlugin:
    """One registry entry."""

    id: str
    name: str
    version: str
    description: str
    file: str  # file name inside the registry's plugins/ dir
    sha256: str
    url: str  # absolute download URL
    author: str = ""
    kinds: list[str] = field(default_factory=list)  # ai | notification | event | other
    homepage: str = ""
    settings_example: str = ""  # TOML snippet users paste into config.toml
    min_tailcam: str = ""


@dataclass
class InstalledPlugin:
    """A drop-in on disk (marketplace-installed or hand-dropped)."""

    id: str  # file stem — what the disabled list and uninstall use
    file: str
    version: str = ""  # from the sidecar; "" for hand-dropped files
    sha256: str = ""
    source: str = "manual"  # "market" | "manual"
    market_id: str = ""  # registry id when installed from the marketplace
    enabled: bool = True
    update_available: str = ""  # newer version in the registry, if any


class PluginMarket:
    def __init__(self, config: PluginsConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cache: list[MarketPlugin] | None = None
        self._cache_ts = 0.0
        self._cache_error = ""

    # -- registry index ------------------------------------------------------
    def catalog(self, *, force: bool = False) -> tuple[list[MarketPlugin], str]:
        """The curated registry: (plugins, error). A fetch failure returns the
        stale cache (if any) plus the error string — the UI shows both."""
        with self._lock:
            fresh = time.monotonic() - self._cache_ts < _CACHE_TTL
            if self._cache is not None and fresh and not force:
                return list(self._cache), self._cache_error
        plugins, error = self._fetch_index()
        with self._lock:
            if plugins is not None:
                self._cache = plugins
                self._cache_ts = time.monotonic()
                self._cache_error = ""
            else:
                self._cache_error = error
            return list(self._cache or []), self._cache_error

    def _fetch_index(self) -> tuple[list[MarketPlugin] | None, str]:
        url = (self._config.registry_url or "").strip()
        if not url.startswith("https://"):
            return None, "registry_url must be https://"
        try:
            resp = httpx.get(url, timeout=_INDEX_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            log.warning("plugin registry fetch failed: %s", exc)
            return None, f"registry unreachable: {exc}"
        try:
            return self._parse_index(raw, url), ""
        except (KeyError, TypeError, ValueError) as exc:
            return None, f"registry index invalid: {exc}"

    @staticmethod
    def _parse_index(raw: dict, index_url: str) -> list[MarketPlugin]:
        base = index_url.rsplit("/", 1)[0]
        out: list[MarketPlugin] = []
        for entry in raw.get("plugins") or []:
            pid = str(entry["id"])
            file = str(entry["file"])
            if not _ID_RE.match(pid) or not _FILE_RE.match(file):
                raise ValueError(f"bad id/file for plugin {pid!r}")
            sha = str(entry["sha256"]).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError(f"bad sha256 for plugin {pid!r}")
            url = str(entry.get("url") or f"{base}/plugins/{file}")
            if not url.startswith("https://"):
                raise ValueError(f"non-https url for plugin {pid!r}")
            out.append(
                MarketPlugin(
                    id=pid,
                    name=str(entry.get("name") or pid),
                    version=str(entry.get("version") or "0"),
                    description=str(entry.get("description") or ""),
                    file=file,
                    sha256=sha,
                    url=url,
                    author=str(entry.get("author") or ""),
                    kinds=[str(k) for k in (entry.get("kinds") or [])],
                    homepage=str(entry.get("homepage") or ""),
                    settings_example=str(entry.get("settings_example") or ""),
                    min_tailcam=str(entry.get("min_tailcam") or ""),
                )
            )
        return out

    # -- install / uninstall ---------------------------------------------------
    def install(self, plugin_id: str) -> InstalledPlugin:
        """Download, verify, and install one registry plugin. Every failure
        raises MarketError with a human-readable reason; nothing partial is
        left on disk."""
        plugins, err = self.catalog()
        entry = next((p for p in plugins if p.id == plugin_id), None)
        if entry is None:
            raise MarketError(err or f"plugin '{plugin_id}' is not in the registry")

        try:
            resp = httpx.get(entry.url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            payload = resp.content
        except Exception as exc:
            raise MarketError(f"download failed: {exc}") from exc

        if len(payload) > _MAX_PLUGIN_BYTES:
            raise MarketError("plugin file is larger than the 1 MB limit")
        digest = hashlib.sha256(payload).hexdigest()
        if digest != entry.sha256:
            raise MarketError(
                "checksum mismatch — the downloaded file does not match the "
                "registry entry, refusing to install"
            )
        try:
            compile(payload.decode("utf-8"), entry.file, "exec")
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise MarketError(f"plugin file is not valid Python: {exc}") from exc

        directory = dropin_dir()
        directory.mkdir(parents=True, exist_ok=True)
        dest = directory / entry.file
        part = dest.with_suffix(dest.suffix + ".part")
        part.write_bytes(payload)
        part.replace(dest)
        meta = {
            "market_id": entry.id,
            "version": entry.version,
            "sha256": entry.sha256,
            "url": entry.url,
            "installed_ts": time.time(),
        }
        dest.with_name(dest.name + _META_SUFFIX).write_text(json.dumps(meta, indent=2))
        log.info("installed plugin %s v%s (%s)", entry.id, entry.version, entry.file)
        return self._installed_entry(dest, plugins)

    def uninstall(self, stem: str) -> bool:
        """Remove a drop-in plugin file (+ sidecar) by file stem. Refuses
        anything that isn't a plain name inside the drop-in folder."""
        if not _ID_RE.match(stem):
            raise MarketError("invalid plugin id")
        directory = dropin_dir()
        target = directory / f"{stem}.py"
        if not target.is_file():
            return False
        target.unlink()
        meta = directory / f"{stem}.py{_META_SUFFIX}"
        meta.unlink(missing_ok=True)
        log.info("uninstalled plugin %s", stem)
        return True

    # -- installed state ---------------------------------------------------------
    def installed(self) -> list[InstalledPlugin]:
        """Every drop-in on disk, with marketplace metadata when present."""
        plugins, _err = (self._cache or [], "") if self._cache is not None else ([], "")
        directory = dropin_dir()
        out: list[InstalledPlugin] = []
        if not directory.is_dir():
            return out
        for file in sorted(directory.glob("*.py")):
            if file.name.startswith("_"):
                continue
            out.append(self._installed_entry(file, plugins))
        return out

    def _installed_entry(self, file: Path, catalog: list[MarketPlugin]) -> InstalledPlugin:
        meta_path = file.with_name(file.name + _META_SUFFIX)
        version = sha = market_id = ""
        source = "manual"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
                version = str(meta.get("version") or "")
                sha = str(meta.get("sha256") or "")
                market_id = str(meta.get("market_id") or "")
                source = "market"
            except (ValueError, OSError):
                pass
        update = ""
        if market_id:
            entry = next((p for p in catalog if p.id == market_id), None)
            if entry is not None and entry.version != version:
                update = entry.version
        return InstalledPlugin(
            id=file.stem,
            file=file.name,
            version=version,
            sha256=sha,
            source=source,
            market_id=market_id,
            enabled=file.stem not in (self._config.disabled or []),
            update_available=update,
        )
