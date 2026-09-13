"""Frozen input identities and input-staging deadline checks."""

from __future__ import annotations

import httpx
import pytest

from tailcam.config import AppConfig
from tailcam.jobs.models import ArtifactRef
from tailcam.persistence.store import Store
from tailcam.storage.models import StorageError
from tailcam.storage.service import StorageService


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    services = []
    for name in ("source", "owner"):
        store = Store(tmp_path / name / "state.db")
        service = StorageService(AppConfig(), store, store.get_node_id())
        service.register_location(str(tmp_path / name / "media"), create=True)
        services.append(service)
    source, owner = services
    source.resolve_peer = lambda _: "http://approved.invalid"
    artifact = owner.put_bytes("model_output", b"weights")
    now = [1000.0]
    monkeypatch.setattr("tailcam.storage.service.time.time", lambda: now[0])
    yield source, artifact, now
    for service in services:
        service.close()


def test_expired_input_deadline_performs_no_metadata_or_file_io(inputs):
    source, artifact, now = inputs
    calls = []
    source._client = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r)))
    with source.workspace_for_task("training", max_bytes=100) as lease:
        with pytest.raises(StorageError, match="deadline") as failure:
            source.materialize_ref(ArtifactRef.from_artifact(artifact), lease, deadline_at=now[0])
        assert failure.value.code == "deadline_exceeded"
        assert calls == []
        assert list(lease.path.iterdir()) == []


@pytest.mark.parametrize("stall_at", ["metadata", "content"])
def test_input_deadline_caps_http_and_removes_uncommitted_bytes(inputs, stall_at):
    source, artifact, now = inputs
    requests = []

    class SlowBody(httpx.SyncByteStream):
        def __iter__(self):
            now[0] += 2
            yield b"weights"

    def handler(request):
        requests.append(request)
        assert 0 < request.extensions["timeout"]["read"] <= 1
        if request.url.path.endswith("/content"):
            return httpx.Response(200, stream=SlowBody())
        if stall_at == "metadata":
            now[0] += 2
        return httpx.Response(200, json=artifact.model_dump(mode="json"))

    source._client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    with source.workspace_for_task("training", max_bytes=100) as lease:
        with pytest.raises(StorageError) as failure:
            source.materialize_ref(ArtifactRef.from_artifact(artifact), lease, deadline_at=1001)
        assert failure.value.code == "deadline_exceeded"
        assert list(lease.path.iterdir()) == []
    assert len(requests) == (1 if stall_at == "metadata" else 2)


def test_frozen_input_rejects_metadata_replacement_before_bytes(inputs):
    source, artifact, _ = inputs
    replaced = artifact.model_copy(update={"sha256": "0" * 64})
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=replaced.model_dump(mode="json"))

    source._client = httpx.Client(transport=httpx.MockTransport(handler))
    with source.workspace_for_task("training", max_bytes=100) as lease:
        with pytest.raises(StorageError) as failure:
            source.materialize_ref(ArtifactRef.from_artifact(artifact), lease)
        assert failure.value.code == "input_changed"
        assert source.catalog.get(artifact.artifact_id) is None
        assert list(lease.path.iterdir()) == []
    assert len(requests) == 1
