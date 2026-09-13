"""Ambiguous remote acceptance and coordinator restart preserve one assignment."""

from __future__ import annotations

import json

import httpx
import pytest

from tailcam.config import AppConfig
from tailcam.jobs.models import (
    JobError,
    JobSpec,
    PlacementPlan,
    ProviderDefinition,
    ResultManifest,
    SafeError,
    WorkerTarget,
)
from tailcam.jobs.service import JobService
from tailcam.jobs.transport import JobTransport
from tailcam.persistence.store import Store


@pytest.fixture
def pair(tmp_path, monkeypatch):
    now = [1000.0]
    services = []
    for name in ("source", "worker"):
        config, store = AppConfig(), Store(tmp_path / name / "state.db")
        config.jobs.lease_seconds = 2
        services.append(JobService(config, store, store.get_node_id(), clock=lambda: now[0]))
    source, worker = services
    target = WorkerTarget(node_id=worker.node_id)
    record = source.submit(
        JobSpec(
            task="timelapse_encode",
            origin_node_id=source.node_id,
            reference={"camera_id": "/dev/video0", "timelapse_id": "7"},
            placement_plan=PlacementPlan(
                task="timelapse_encode", requested_target=target, selected_target=target
            ),
        ),
        idempotency_key="same-render",
    )
    monkeypatch.setattr(
        "tailcam.jobs.transport.time.sleep", lambda _: now.__setitem__(0, now[0] + 0.1)
    )
    return source, worker, record, now


def finish(worker):
    lease = worker.claim_local()
    assert lease is not None
    worker.start_attempt(lease)
    permit = worker.prepare_result(lease, ResultManifest(result={"frames": 3}))
    worker.commit_result(permit, [])


def transport(worker, handler):
    return JobTransport(
        lambda node: "http://approved.invalid" if node == worker.node_id else None,
        client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )


def test_lost_acceptance_ack_reuses_exact_assignment_and_never_reexecutes(pair):
    source, worker, record, _ = pair
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        accepted = worker.accept_remote(
            body["spec"], coordinator_node_id=body["coordinator_node_id"]
        )
        if len(requests) == 1:
            finish(worker)
            raise httpx.ReadTimeout("lost reply", request=request)
        return httpx.Response(200, json=worker.get(accepted.job_id).model_dump(mode="json"))

    lease = source._claim("coordinator", remote=True)
    transport(worker, handler).execute(lease, source)
    assert requests[0] == requests[1]
    assert requests[0]["spec"]["reference"] == {
        "camera_id": "/dev/video0",
        "coordinator_job_id": record.job_id,
        "coordinator_stage_id": "main",
    }
    assert worker.spec(worker.list()[0].job_id).reference["camera_id"] == "/dev/video0"
    assert len(worker.list()) == 1
    assert worker.list()[0].stages[0].attempt == 1
    assert source.get(record.job_id).state == "succeeded"


def test_coordinator_restart_reuses_original_deadline_and_selected_outputs(pair):
    source, worker, record, now = pair
    requests = []

    class CoordinatorCrash(Exception):
        pass

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        accepted = worker.accept_remote(
            body["spec"], coordinator_node_id=body["coordinator_node_id"]
        )
        if len(requests) == 1:
            finish(worker)
            raise CoordinatorCrash
        return httpx.Response(200, json=worker.get(accepted.job_id).model_dump(mode="json"))

    with pytest.raises(CoordinatorCrash):
        transport(worker, handler).execute(source._claim("old", remote=True), source)
    now[0] += 3
    restarted = JobService(
        source.config, Store(source.journal.store.db_path), source.node_id, clock=source.clock
    )
    assert restarted.recover() == 1
    now[0] += 3
    lease = restarted._claim("new", remote=True)
    assert lease.attempt == 2
    transport(worker, handler).execute(lease, restarted)
    assert requests[0] == requests[1]
    assert worker.spec(worker.list()[0].job_id).reference["camera_id"] == "/dev/video0"
    assert len(worker.list()) == 1
    assert worker.list()[0].stages[0].attempt == 1
    assert restarted.get(record.job_id).state == "succeeded"


def test_unacknowledged_remote_cancel_stays_requested_until_worker_ack(pair):
    source, worker, record, _ = pair
    cancellations = []
    remote_lease = None

    def handler(request):
        nonlocal remote_lease
        if request.url.path.endswith("/execute"):
            body = json.loads(request.content)
            accepted = worker.accept_remote(
                body["spec"], coordinator_node_id=body["coordinator_node_id"]
            )
            remote_lease = worker.claim_local()
            worker.start_attempt(remote_lease)
            source.request_cancel(record.job_id)
            return httpx.Response(200, json=worker.get(accepted.job_id).model_dump(mode="json"))
        if request.url.path.endswith("/cancel"):
            cancellations.append(source.get(record.job_id).state)
            if len(cancellations) == 1:
                return httpx.Response(503)
            worker.request_cancel(remote_lease.job_id)
            worker.fail(remote_lease, SafeError(code="cancelled", detail="Owned child stopped"))
        assert source.get(record.job_id).state == "cancel_requested"
        return httpx.Response(200, json=worker.get(remote_lease.job_id).model_dump(mode="json"))

    transport(worker, handler).execute(source._claim("coordinator", remote=True), source)
    assert cancellations == ["cancel_requested", "cancel_requested"]
    assert source.get(record.job_id).state == "cancelled"


def test_delegated_provider_uses_frozen_definition_without_worker_registry(pair):
    source, worker, existing, _ = pair
    source.request_cancel(existing.job_id)
    provider = source.placement.register_provider(
        ProviderDefinition(
            name="Approved Ollama", base_url="http://provider.invalid", model="vision:latest"
        )
    )
    target = WorkerTarget(node_id=worker.node_id, provider_id=provider.provider_id)
    record = source.submit(
        JobSpec(
            task="motion_description",
            origin_node_id=source.node_id,
            placement_plan=PlacementPlan(
                task="motion_description", requested_target=target, selected_target=target
            ),
        ),
        idempotency_key="provider",
    )

    def handler(request):
        body = json.loads(request.content)
        accepted = worker.accept_remote(
            body["spec"], coordinator_node_id=body["coordinator_node_id"]
        )
        snapshot = worker.spec(accepted.job_id).stages[0].parameters["_provider"]
        assert snapshot == provider.model_dump(mode="json")
        assert worker.providers() == []
        finish(worker)
        return httpx.Response(200, json=worker.get(accepted.job_id).model_dump(mode="json"))

    transport(worker, handler).execute(source._claim("coordinator", remote=True), source)
    assert source.get(record.job_id).state == "succeeded"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "http://secret.invalid"}),
        httpx.Response(200, headers={"content-encoding": "gzip"}),
        httpx.Response(200, content=b"x" * (2 * 1024**2 + 1)),
    ],
)
def test_transport_refuses_redirects_encoding_and_oversized_responses(pair, response):
    _, worker, _, _ = pair
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["accept-encoding"] == "identity"
        return response

    with pytest.raises(JobError) as failure:
        transport(worker, handler).request(worker.node_id, "GET", "/api/v1/jobs")
    assert "secret.invalid" not in failure.value.detail
    assert len(calls) == 1
