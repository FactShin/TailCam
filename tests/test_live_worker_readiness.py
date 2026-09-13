"""Live Auto placement uses only recent, validated execution on this worker."""

from __future__ import annotations

import base64
import builtins
import time
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest

from tailcam.jobs.models import JobError, PlacementPlan, ResourceBudget, TaskRoute, WorkerTarget
from tailcam.workloads.live import LiveRequest, _Session


def _plan(node_id):
    return PlacementPlan(
        task="live_detection",
        requested_target=WorkerTarget(node_id=node_id),
        selected_target=WorkerTarget(node_id=node_id),
        budget=ResourceBudget(memory_bytes=16 * 1024**2, workspace_bytes=1024, wall_seconds=5),
    )


def _request(node_id):
    ok, jpeg = cv2.imencode(".jpg", np.zeros((4, 4, 3), np.uint8))
    assert ok
    return LiveRequest(
        session_id=str(uuid4()),
        request_id=str(uuid4()),
        target_node_id=node_id,
        coordinator_node_id=str(uuid4()),
        plan=_plan(node_id),
        jpeg=base64.b64encode(jpeg).decode(),
        deadline_at=time.time() + 5,
    )


def _local_task(context):
    return next(
        task for task in context.workload_peers.local().tasks if task.task == "live_detection"
    )


@pytest.fixture
def live_worker(context, monkeypatch):
    result = {"available": True, "outcome": "succeeded", "predictions": [{"boxes": []}]}

    def open_session(request):
        return _Session(
            plan=request.plan,
            lease=SimpleNamespace(check=lambda: 0, release=lambda: None),
            actor=SimpleNamespace(infer=lambda *a, **kw: dict(result), close=lambda: None),
            model_name="Observed model",
            coordinator_node_id=request.coordinator_node_id,
        )

    monkeypatch.setattr(context.workloads, "_open_session", open_session)
    monkeypatch.setattr(
        context.jobs.placement, "_workers", lambda: [context.workload_peers.local()]
    )
    return context, result


def test_auto_selects_recent_local_live_success_without_passive_model_or_network_work(
    live_worker, monkeypatch
):
    context, result = live_worker
    policy = context.jobs.get_policy()
    policy.routes["live_detection"] = TaskRoute(
        mode="auto", approved_node_ids=[context.node_id], budget=_plan(context.node_id).budget
    )
    context.jobs.set_policy(policy, expected_revision=policy.revision)
    with pytest.raises(JobError, match="No permitted worker"):
        context.jobs.placement.plan("live_detection")
    # An incoming request from another coordinator exercises this worker; it never
    # populates the source-camera status map, so that map cannot establish readiness.
    request = _request(context.node_id)
    context.workloads.execute_live(request)
    assert context.workloads.status() == {}
    observed = context.workloads.local_live_observation()
    assert observed["worker_node_id"] == context.node_id
    assert 0 <= time.time() - observed["completed_at"] < 5
    observed.clear()
    assert context.workloads.local_live_observation()  # callers cannot mutate evidence

    original_import = builtins.__import__

    def safe_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"torch", "ultralytics", "cv2"}
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("passive readiness touched a runtime or network")

    with monkeypatch.context() as passive:
        passive.setattr(builtins, "__import__", safe_import)
        passive.setattr(context.storage_peers, "refresh", forbidden)
        passive.setattr(context.workloads, "_open_session", forbidden)
        passive.setattr(context.workloads, "_model_inputs", forbidden)
        passive.setattr(context.manager, "discover", forbidden)
        assert _local_task(context).state == "ready"
        selected = context.jobs.placement.plan("live_detection")
        assert selected.selected_target.node_id == context.node_id


@pytest.mark.parametrize("age", [1800, 1801, -1])
def test_stale_or_future_live_observation_does_not_advertise_ready(live_worker, monkeypatch, age):
    context, _ = live_worker
    context.workloads.execute_live(_request(context.node_id))
    observed_at = context.workloads.local_live_observation()["completed_at"]
    monkeypatch.setattr("tailcam.web.workload_peers.time.time", lambda: observed_at + age)
    assert _local_task(context).state == "unchecked"


def test_failed_local_inference_invalidates_previous_success(live_worker):
    context, result = live_worker
    request = _request(context.node_id)
    context.workloads.execute_live(request)
    assert _local_task(context).state == "ready"
    result["available"] = False
    with pytest.raises(JobError, match="invalid or mismatched"):
        context.workloads.execute_live(request.model_copy(update={"request_id": str(uuid4())}))
    assert context.workloads.local_live_observation() == {}
    assert _local_task(context).state == "unchecked"


@pytest.mark.parametrize("succeeded", [False, True])
def test_remote_live_observation_never_promotes_source_runtime(context, monkeypatch, succeeded):
    remote_id = str(uuid4())
    monkeypatch.setattr(context.workloads, "_plan", lambda *a, **kw: _plan(remote_id))

    def remote_response(*args, **kwargs):
        if not succeeded:
            raise JobError("worker_unavailable", "Remote worker unavailable.")
        return {
            "available": True,
            "outcome": "succeeded",
            "worker_node_id": remote_id,
            "session_id": kwargs["json"]["session_id"],
            "model": "Remote model",
            "execution_ms": 1,
            "queue_ms": 0,
            "predictions": [{"boxes": []}],
            "device": "cuda",
        }

    monkeypatch.setattr(context.job_transport, "request", remote_response)
    image = np.zeros((4, 4, 3), np.uint8)
    if succeeded:
        context.workloads.detect(image)
        assert context.workloads.status()["worker_node_id"] == remote_id
    else:
        with pytest.raises(JobError):
            context.workloads.detect(image)
    assert context.workloads.local_live_observation() == {}
    assert _local_task(context).state == "unchecked"
    assert context.workload_peers.local().gpu_observed is False
