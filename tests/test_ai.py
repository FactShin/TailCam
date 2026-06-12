import httpx
import numpy as np

from anycam.ai import analyzer as ai
from anycam.config import AIConfig


def _frame():
    return np.zeros((48, 64, 3), dtype=np.uint8)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


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


def test_analyze_parses_ollama(monkeypatch):
    body = '{"label":"person","confidence":0.8,"description":"a man"}'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        return httpx.Response(200, json={"response": body})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: _client(handler).post(url, **kw))
    an = ai.OllamaAnalyzer(AIConfig(enabled=True))
    result = an.analyze(_frame())
    assert result is not None and result.label == "person" and result.confidence == 0.8


def test_analyze_tolerates_garbage(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "not json at all"})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: _client(handler).post(url, **kw))
    an = ai.OllamaAnalyzer(AIConfig(enabled=True))
    assert an.analyze(_frame()) is None


def test_analyze_tolerates_connection_error(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("ollama down")

    monkeypatch.setattr(httpx, "post", boom)
    an = ai.OllamaAnalyzer(AIConfig(enabled=True))
    assert an.analyze(_frame()) is None


def test_health_reports_model_present(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "moondream:latest"}]})

    monkeypatch.setattr(httpx, "get", lambda url, **kw: _client(handler).get(url, **kw))
    an = ai.OllamaAnalyzer(AIConfig(enabled=True, model="moondream"))
    reachable, model = an.health()
    assert reachable is True and model == "moondream"
