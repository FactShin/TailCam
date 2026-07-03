"""OpenAI-compatible vision analyzer for TailCam motion events.

Points TailCam's AI motion analysis at ANY OpenAI-compatible chat-completions
endpoint: OpenAI itself, OpenRouter, LM Studio, llama.cpp server, vLLM, or
Ollama's ``/v1`` — so you can label events with whatever vision model you like.

Settings (``config.toml``)::

    [plugins.settings.openai_analyzer]
    base_url = "https://api.openai.com/v1"   # or http://localhost:1234/v1 (LM Studio) …
    api_key = "sk-…"                          # blank for local servers that don't check
    model = "gpt-4o-mini"
    timeout = 30.0

Activate it: Settings → AI (or ``[ai] provider = "openai"``) and restart.
The master AI switch (``ai.enabled``) still controls whether analysis runs.
"""

from __future__ import annotations

__plugin__ = {
    "id": "openai_analyzer",
    "name": "OpenAI-compatible analyzer",
    "version": "1.0.0",
    "description": (
        "Label motion events with any OpenAI-compatible vision endpoint — OpenAI, "
        "OpenRouter, LM Studio, llama.cpp, vLLM, or Ollama's /v1."
    ),
    "author": "TailCam community",
    "kinds": ["ai"],
    "settings_example": (
        '[plugins.settings.openai_analyzer]\n'
        'base_url = "https://api.openai.com/v1"\n'
        'api_key = "sk-..."\n'
        'model = "gpt-4o-mini"\n'
    ),
}

import base64
import json

import cv2
import httpx

from tailcam.plugins.sdk import PluginInfo, get_logger, hookimpl, plugin_settings

log = get_logger("plugin.openai_analyzer")

_PROMPT = (
    "You are a security camera analyst. Look at this single frame and respond ONLY "
    'with JSON: {"label": one short lowercase noun for the main subject (e.g. person, '
    'dog, car, package, nothing), "confidence": a number 0-1, "description": a short '
    "phrase}. No other text."
)


class _OpenAIAnalyzer:
    """Duck-types tailcam.ai.analyzer.FrameAnalyzer."""

    def __init__(self, ai_config) -> None:
        self._ai = ai_config  # live reference; .enabled is the master switch

    @property
    def enabled(self) -> bool:
        return bool(self._ai.enabled)

    def analyze(self, image):
        from tailcam.ai.analyzer import Analysis

        s = plugin_settings("openai_analyzer")
        base = str(s.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        model = str(s.get("model") or "gpt-4o-mini")
        timeout = float(s.get("timeout") or 30.0)
        headers = {}
        if s.get("api_key"):
            headers["Authorization"] = f"Bearer {s['api_key']}"

        h, w = image.shape[:2]
        if w > 768:
            image = cv2.resize(image, (768, max(1, int(h * 768 / w))))
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        data_uri = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

        body = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
            "max_tokens": 120,
        }
        try:
            resp = httpx.post(
                f"{base}/chat/completions", json=body, headers=headers, timeout=timeout
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            log.warning("openai analyzer request failed: %s", exc)
            return None
        try:
            # Models sometimes wrap JSON in code fences; strip them.
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            data = json.loads(text)
            label = str(data.get("label") or "").strip().lower()[:40]
            if not label:
                return None
            conf = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
            desc = str(data.get("description") or "").strip()[:200]
            return Analysis(label=label, description=desc, confidence=conf)
        except (ValueError, TypeError) as exc:
            log.warning("openai analyzer returned unparseable JSON: %s", exc)
            return None


class OpenAIProvider:
    id = "openai"
    name = "OpenAI-compatible"
    description = "Any OpenAI-compatible /v1 vision endpoint (cloud or local)."

    def build(self, config):
        return _OpenAIAnalyzer(config)


@hookimpl
def tailcam_analyzer_providers():
    return [OpenAIProvider()]


@hookimpl
def tailcam_plugin_info():
    return [
        PluginInfo(
            id="openai_analyzer",
            name="OpenAI-compatible analyzer",
            kind="ai",
            description=__plugin__["description"],
            version=__plugin__["version"],
        )
    ]
