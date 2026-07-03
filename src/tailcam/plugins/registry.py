"""Plugin registry — discovers and aggregates TailCam plugins via pluggy.

Built-in plugins are registered directly. External plugins are discovered two
ways, so they can be "added at any time":

1. **Entry points** — any installed package exposing the ``tailcam`` entry-point
   group (``pip install tailcam-plugin-foo``).
2. **Drop-in folder** — single ``*.py`` files in ``<config-dir>/plugins/``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pluggy

from tailcam import paths
from tailcam.logging_setup import get_logger
from tailcam.plugins import hookspecs
from tailcam.plugins.builtin.ai_providers import OllamaPlugin
from tailcam.plugins.builtin.channels import ChannelsPlugin
from tailcam.plugins.hookspecs import PROJECT, PluginInfo

log = get_logger(__name__)


def _flatten(results: list) -> list:
    out: list = []
    for chunk in results:
        if chunk:
            out.extend(chunk)
    return out


def dropin_dir() -> Path:
    """Where drop-in / marketplace plugins live: ``<config-dir>/plugins/``."""
    return paths.config_dir() / "plugins"


class PluginRegistry:
    def __init__(
        self,
        *,
        load_external: bool = True,
        load_dropins: bool = True,
        disabled: list[str] | None = None,
        dropin_dir=None,
    ) -> None:
        self._disabled = set(disabled or [])
        self._errors: list[str] = []
        self._external: list[str] = []
        self._skipped: list[str] = []  # present but disabled by the user
        self._pm = pluggy.PluginManager(PROJECT)
        self._pm.add_hookspecs(hookspecs)
        self._pm.register(OllamaPlugin(), name="builtin.ollama")
        self._pm.register(ChannelsPlugin(), name="builtin.channels")
        if load_external:
            self._load_entrypoints()
            if load_dropins:
                self._load_dropins(dropin_dir)

    # -- discovery ---------------------------------------------------------
    def _load_entrypoints(self) -> None:
        try:
            # The disabled list holds entry-point names / drop-in file stems;
            # blocking prevents a disabled plugin's code from even registering.
            for name in self._disabled:
                self._pm.set_blocked(name)
            count = self._pm.load_setuptools_entrypoints(PROJECT)
            if count:
                log.info("Loaded %d plugin(s) from entry points", count)
        except Exception as exc:  # a bad plugin shouldn't break startup
            log.warning("Plugin entry-point loading failed: %s", exc)
            self._errors.append(f"entry points: {exc}")

    def _load_dropins(self, dir_override) -> None:
        directory = dir_override or dropin_dir()
        try:
            if not directory.is_dir():
                return
            for file in sorted(directory.glob("*.py")):
                if file.name.startswith("_"):
                    continue
                if file.stem in self._disabled:
                    self._skipped.append(file.stem)
                    log.info("Drop-in plugin %s is disabled; not loaded", file.name)
                    continue
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"tailcam_dropin_{file.stem}", file
                    )
                    if spec is None or spec.loader is None:
                        continue
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    self._pm.register(module, name=f"dropin.{file.stem}")
                    self._external.append(file.stem)
                    log.info("Loaded drop-in plugin %s", file.name)
                except Exception as exc:
                    log.warning("Drop-in plugin %s failed: %s", file.name, exc)
                    self._errors.append(f"{file.name}: {exc}")
        except OSError as exc:
            self._errors.append(f"plugins dir: {exc}")

    # -- access ------------------------------------------------------------
    def analyzer_providers(self) -> list:
        return _flatten(self._pm.hook.tailcam_analyzer_providers())

    def notification_channels(self) -> list:
        return _flatten(self._pm.hook.tailcam_notification_channels())

    def event_hooks(self) -> list:
        return _flatten(self._pm.hook.tailcam_event_hooks())

    def plugin_infos(self) -> list[PluginInfo]:
        return _flatten(self._pm.hook.tailcam_plugin_info())

    @property
    def loaded_dropins(self) -> list[str]:
        """File stems of drop-in plugins that loaded this run."""
        return list(self._external)

    @property
    def skipped_dropins(self) -> list[str]:
        """File stems present on disk but disabled by the user."""
        return list(self._skipped)

    def analyzer_provider(self, provider_id: str):
        for provider in self.analyzer_providers():
            if provider.id == provider_id:
                return provider
        return None

    def is_disabled(self, plugin_id: str) -> bool:
        return plugin_id in self._disabled

    @property
    def errors(self) -> list[str]:
        return self._errors
