"""Built-in AI analyzer provider: Ollama."""

from __future__ import annotations

from tailcam.ai.analyzer import FrameAnalyzer, OllamaAnalyzer
from tailcam.config import AIConfig
from tailcam.plugins.hookspecs import PluginInfo, hookimpl


class OllamaProvider:
    id = "ollama"
    name = "Ollama (local AI)"
    description = "Local vision-model motion analysis via Ollama (moondream, llava, …)."

    def build(self, config: AIConfig) -> FrameAnalyzer:
        return OllamaAnalyzer(config)


class OllamaPlugin:
    @hookimpl
    def tailcam_analyzer_providers(self) -> list:
        return [OllamaProvider()]

    @hookimpl
    def tailcam_plugin_info(self) -> list:
        return [
            PluginInfo(
                id="builtin-ollama",
                name="Ollama analyzer",
                kind="ai",
                description="Local vision-model motion analysis via Ollama.",
                builtin=True,
            )
        ]
