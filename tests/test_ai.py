import httpx
import numpy as np

from tailcam.ai import analyzer as ai
from tailcam.config import AIConfig


def _frame():
    return np.zeros((48, 64, 3), dtype=np.uint8)


def _analyzer(config: AIConfig, handler) -> ai.OllamaAnalyzer:
    """An analyzer whose shared HTTP client is backed by a mock transport."""
    an = ai.OllamaAnalyzer(config)
    an._client = httpx.Client(transport=httpx.MockTransport(handler))
    return an


def test_coerce_valid():
    a = ai._coerce({"label": "Person", "confidence": 0.91, "description": "a person at the door"})
    assert a is not None and a.label == "person" and a.confidence == 0.91


def test_coerce_extracts_label_from_phrase():
    a = ai._coerce({"label": "a person walking", "confidence": 2, "description": ""})
    assert a is not None and a.label == "person"
    assert a.confidence == 1.0  # clamped


def test_coerce_rejects_unknown():
    assert ai._coerce({"label": "spaceship", "confidence": 0.5}) is None


def test_analyze_disabled_returns_none():
    an = ai.OllamaAnalyzer(AIConfig(enabled=False))
    assert an.analyze(_frame()) is None


def test_analyze_parses_ollama():
    body = '{"label":"person","confidence":0.8,"description":"a man"}'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        return httpx.Response(200, json={"response": body})

    an = _analyzer(AIConfig(enabled=True), handler)
    result = an.analyze(_frame())
    assert result is not None and result.label == "person" and result.confidence == 0.8


def test_analyze_tolerates_garbage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "not json at all"})

    an = _analyzer(AIConfig(enabled=True), handler)
    assert an.analyze(_frame()) is None


def test_analyze_tolerates_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ollama down")

    an = _analyzer(AIConfig(enabled=True), handler)
    assert an.analyze(_frame()) is None


def test_health_reports_model_present():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "moondream:latest"}]})

    an = _analyzer(AIConfig(enabled=True, model="moondream"), handler)
    reachable, model = an.health()
    assert reachable is True and model == "moondream"


def test_health_reports_reachability_even_when_disabled():
    """"Analysis off" and "Ollama down" are different states — health must not
    conflate them (the dashboard shows both independently)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "moondream:latest"}]})

    an = _analyzer(AIConfig(enabled=False, model="moondream"), handler)
    reachable, model = an.health()
    assert reachable is True and model == "moondream"
