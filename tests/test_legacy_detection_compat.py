"""The saved legacy peer route remains bounded, local-only and observable."""

from __future__ import annotations

import httpx
import numpy as np
import pytest

from tailcam.ai.remote import RemoteDetector


@pytest.fixture
def frame():
    return np.zeros((4, 4, 3), np.uint8)


def test_legacy_peer_transport_disables_proxy_and_redirects(monkeypatch, frame):
    original = httpx.Client
    options = {}
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["accept-encoding"] == "identity"
        assert request.url.path == "/api/detect-image"
        return httpx.Response(302, headers={"location": "http://elsewhere.invalid/secret"})

    def client(**kwargs):
        options.update(kwargs)
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", client)
    detector = RemoteDetector(lambda: "http://approved.invalid")
    try:
        assert detector.detect(frame) is None
        assert options["trust_env"] is False
        assert options["follow_redirects"] is False
        assert len(requests) == 1
    finally:
        detector._client.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * (256 * 1024 + 1)),
        httpx.Response(200, content=b"private response", headers={"content-encoding": "br"}),
        httpx.Response(500, content=b"private response"),
        httpx.Response(200, json=["private response"]),
    ],
)
def test_invalid_peer_body_is_bounded_and_error_is_safe(response, frame):
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        detector = RemoteDetector(lambda: "http://approved.invalid", client=client)
        assert detector.detect(frame) is None
        assert detector.last_error == (
            "Selected detection node is unavailable or returned an invalid result"
        )
        assert detector._observation == {}
        assert not detector.available


def test_peer_exception_and_endpoint_credentials_are_never_exposed(frame):
    calls = []

    def handler(request):
        calls.append(request)
        raise RuntimeError("private credential")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for base in ("http://approved.invalid", "http://user:private@approved.invalid"):
            detector = RemoteDetector(lambda base=base: base, client=client)
            assert detector.detect(frame) is None
            assert "private" not in detector.last_error
        assert len(calls) == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 2, True, "0.5"])
def test_invalid_box_numbers_do_not_become_successful_empty_detection(frame, value):
    detector = RemoteDetector(lambda: "http://approved.invalid")
    detector._post = lambda *_: {
        "boxes": [{"label": "cat", "confidence": value, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}]
    }
    assert detector.detect(frame) is None


def test_old_peer_without_model_metadata_does_not_invent_observation(frame):
    responses = iter(
        [
            {"boxes": [], "model_name": "Actual model"},
            {"boxes": []},
        ]
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=next(responses)))
    ) as client:
        detector = RemoteDetector(lambda: "http://approved.invalid", client=client)
        assert detector.detect(frame) == []
        assert detector.status()["model_name"] == "Actual model"
        assert detector.detect(frame) == []
        assert "model_name" not in detector.status()
        assert detector.status()["round_trip_ms"] >= 0
