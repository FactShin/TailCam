"""A reconnecting mock host cannot exceed the approved experiment envelope."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from tailcam.config import AppConfig
from tailcam.jobs.models import JobError, JobSpec, ResultManifest, SafeError
from tailcam.jobs.service import JobService
from tailcam.persistence.models import DatasetRecord, ModelRecord
from tailcam.tailscale.client import TAILCAM_APP_CAPABILITY
from tailcam.training.supervisor import TrainingSupervisor
from tailcam.training.supervisor_models import AgentHeartbeat, ExperimentRequest, SupervisionCreate
from tailcam.web.app import create_app


@pytest.fixture
def supervision_env(store):
    now = [1000.0]
    node = store.get_node_id()
    config = AppConfig()
    dataset = store.add_dataset(
        DatasetRecord(id=None, name="Reviewed", task="classification", created_ts=1000)
    )
    model = store.add_model(
        ModelRecord(
            id=None,
            name="Candidate base",
            kind="trained",
            path="",
            classes_json='["ok"]',
            base_model="approved.pt",
            metrics_json="{}",
            created_ts=1000,
        )
    )
    jobs = JobService(config, store, node, clock=lambda: now[0])
    prepared = []

    def prepare(**kwargs):
        prepared.append(kwargs)
        return JobSpec(
            job_id=kwargs["job_id"],
            origin_node_id=node,
            task="training",
            resource_budget=kwargs["budget"],
            max_attempts=kwargs["max_attempts"],
            parameters={"epochs": kwargs["epochs"], "seed": kwargs["seed"]},
            reference={"supervision_id": kwargs["supervision_id"]},
        )

    training = SimpleNamespace(
        dataset_revision=lambda _id: "a" * 64,
        prepare_training_job=prepare,
        project_job=lambda job: None,
    )
    supervisor = TrainingSupervisor(store, jobs, training, node, clock=lambda: now[0])
    request = SupervisionCreate.model_validate(
        {
            "objective": {
                "name": "Improve reviewed printer examples",
                "task": "classification",
                "dataset_id": dataset,
                "dataset_revision": "a" * 64,
                "camera_ids": ["printer"],
                "classes": ["ok"],
                "success_criteria": [{"metric": "accuracy", "threshold": 0.9}],
                "evaluation_reference": "reviewed-session-split-v1",
            },
            "policy": {
                "allowed_model_ids": [model],
                "allowed_worker_node_ids": [node],
                "max_experiments": 3,
                "total_wall_seconds": 60,
                "experiment_budget": {"wall_seconds": 20},
            },
        }
    )
    record = supervisor.create(request, owner="owner@example.test")
    return SimpleNamespace(
        supervisor=supervisor,
        jobs=jobs,
        training=training,
        prepared=prepared,
        node=node,
        model=model,
        dataset=dataset,
        record=record,
        now=now,
        store=store,
        request=request,
    )


def experiment(env, key="one", **changes):
    return ExperimentRequest.model_validate(
        {
            "idempotency_key": key,
            "base_model_id": env.model,
            "worker_node_id": env.node,
            "epochs": 2,
            "image_size": 224,
            "seed": 0,
            "reason": "Compare the approved setting",
            **changes,
        }
    )


def finish_job(env, accuracy=0.9):
    lease = env.jobs.claim_local()
    assert lease is not None
    env.jobs.start_attempt(lease)
    permit = env.jobs.prepare_result(
        lease, ResultManifest(result={"metrics": {"accuracy": accuracy}})
    )
    env.jobs.commit_result(permit, [])


def test_stop_between_preparation_and_enqueue_reports_stopped(supervision_env, monkeypatch):
    env = supervision_env
    original = env.jobs.submit

    def stop_then_submit(*args, **kwargs):
        env.supervisor.stop(env.record.supervision_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(env.jobs, "submit", stop_then_submit)
    record = env.supervisor.experiment(env.record.supervision_id, experiment(env))
    assert record.state == "stopped"
    assert record.experiments[0].state == "cancelled"
    assert env.jobs.list() == []


def test_mock_host_three_runs_disconnect_reconnect_and_report(supervision_env):
    env = supervision_env
    ident = env.record.supervision_id
    env.supervisor.heartbeat(ident, AgentHeartbeat(session_id="host-first", decision="inspect"))
    for index in range(3):
        req = experiment(env, str(index))
        record = env.supervisor.experiment(ident, req)
        assert record.remaining_experiments == 2 - index
        job_id = record.current_job_id
        # A new service and host session observes the same durable reservation.
        env.supervisor = TrainingSupervisor(
            env.store, env.jobs, env.training, env.node, clock=lambda: env.now[0]
        )
        replay = env.supervisor.experiment(ident, req)
        assert replay.current_job_id == job_id
        assert len(env.prepared) == index + 1
        finish_job(env, accuracy=0.8 + index / 20)
    report = env.supervisor.report(ident)
    assert report["supervision"].state == "completed"
    assert report["supervision"].remaining_wall_seconds == 0
    assert len(report["comparison"]) == 3
    assert report["best_candidate"] == report["comparison"][2]["experiment_id"]
    assert report["activation_performed"] is False
    assert len(env.store.list_models()) == 1
    assert all(item["parameters"]["seed"] == 0 for item in report["comparison"])
    assert report["limitations"]
    with pytest.raises(JobError):
        env.supervisor.experiment(ident, experiment(env, "fourth"))


def test_concurrent_hosts_reserve_once_and_cannot_exceed_concurrency(supervision_env):
    env = supervision_env
    barrier = threading.Barrier(8)

    def submit(index):
        barrier.wait(timeout=5)
        try:
            return env.supervisor.experiment(env.record.supervision_id, experiment(env, str(index)))
        except JobError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sum(not isinstance(item, str) for item in results) == 1
    assert results.count("experiment_active") == 7
    assert len(env.prepared) == 1
    assert len(env.jobs.list()) == 1
    assert env.supervisor.get(env.record.supervision_id).remaining_wall_seconds == 40


def test_reused_idempotency_key_cannot_change_parameters(supervision_env):
    env = supervision_env
    env.supervisor.experiment(env.record.supervision_id, experiment(env))
    with pytest.raises(JobError, match="different experiment"):
        env.supervisor.experiment(env.record.supervision_id, experiment(env, epochs=3))
    assert len(env.prepared) == 1


@pytest.mark.parametrize(
    "change", [{"base_model_id": 999}, {"epochs": 11}, {"image_size": 1024}, {"seed": 42}]
)
def test_policy_rejected_before_preparation_or_reservation(supervision_env, change):
    env = supervision_env
    with pytest.raises(JobError) as exc:
        env.supervisor.experiment(env.record.supervision_id, experiment(env, **change))
    assert exc.value.code == "experiment_outside_policy"
    assert not env.prepared
    assert env.supervisor.get(env.record.supervision_id).remaining_experiments == 3


def test_heartbeat_is_separate_from_worker_progress_and_events_are_replayable(supervision_env):
    env = supervision_env
    ident = env.record.supervision_id
    env.supervisor.heartbeat(ident, AgentHeartbeat(session_id="host"))
    env.supervisor.experiment(ident, experiment(env))
    assert env.supervisor.get(ident).agent_connected
    env.now[0] += 301
    record = env.supervisor.get(ident)
    assert not record.agent_connected
    assert record.current_job_id is not None
    events = env.supervisor.events(ident)
    assert len({e["event_id"] for e in events["items"]}) == len(events["items"])
    assert env.supervisor.events(ident, after=events["cursor"])["items"] == []


def test_stop_during_preparation_never_starts_worker(supervision_env):
    env = supervision_env
    entered, resume = threading.Event(), threading.Event()
    prepare = env.training.prepare_training_job

    def slow_prepare(**kwargs):
        entered.set()
        assert resume.wait(timeout=5)
        return prepare(**kwargs)

    env.training.prepare_training_job = slow_prepare
    with ThreadPoolExecutor(max_workers=1) as pool:
        submitted = pool.submit(
            env.supervisor.experiment, env.record.supervision_id, experiment(env)
        )
        assert entered.wait(timeout=5)
        assert env.supervisor.stop(env.record.supervision_id).state == "stop_requested"
        resume.set()
        assert submitted.result(timeout=5).state == "stopped"
    assert not env.jobs.list()


def test_unknown_failure_stops_remaining_experiments(supervision_env):
    env = supervision_env
    env.supervisor.experiment(env.record.supervision_id, experiment(env))
    lease = env.jobs.claim_local()
    env.jobs.start_attempt(lease)
    env.jobs.fail(lease, SafeError(code="backend_failure", detail="Worker failed"))
    record = env.supervisor.get(env.record.supervision_id)
    assert record.state == "failed"
    assert record.remaining_experiments == 2
    with pytest.raises(JobError):
        env.supervisor.experiment(record.supervision_id, experiment(env, "next"))


def test_stale_dataset_approval_rejected_and_activation_unrepresentable(supervision_env):
    env = supervision_env
    env.training.dataset_revision = lambda _: "b" * 64
    with pytest.raises(JobError) as exc:
        env.supervisor.create(env.request, owner="owner")
    assert exc.value.code == "dataset_revision_changed"
    data = env.request.model_dump()
    data["policy"]["activation_enabled"] = True
    with pytest.raises(ValueError):
        SupervisionCreate.model_validate(data)


def _headers(roles):
    return {
        "tailscale-user-login": "supervisor@example.test",
        "tailscale-app-capabilities": json.dumps({TAILCAM_APP_CAPABILITY: [{"roles": roles}]}),
    }


async def _mcp(http, name, arguments):
    response = await http.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    assert payload["result"].get("isError") is False, payload
    return payload["result"]["structuredContent"]


async def test_real_mcp_boundary_bounded_series_reconnect_and_no_activation(
    supervision_env,
    context,
    monkeypatch,
):
    env = supervision_env
    monkeypatch.setattr(context, "supervisor", env.supervisor)
    context.config.mcp.http_enabled = True
    context.config.training.active_model_id = env.model
    app = create_app(context.config, context=context)
    ident = env.record.supervision_id
    for index in range(3):
        # Each connection is a new stateless MCP host; durable IDs carry recovery.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
            headers=_headers(["viewer", "operator"]),
        ) as http:
            state = await _mcp(http, "get_training_supervision", {"supervision_id": ident})
            assert state["remaining_experiments"] == 3 - index
            args = {"supervision_id": ident, "experiment": experiment(env, str(index)).model_dump()}
            first = await _mcp(http, "submit_training_experiment", args)
            replay = await _mcp(http, "submit_training_experiment", args)
            assert first["current_job_id"] == replay["current_job_id"]
            await _mcp(
                http,
                "heartbeat_training_supervisor",
                {
                    "supervision_id": ident,
                    "heartbeat": {"session_id": f"host-{index}"},
                },
            )
        finish_job(env, accuracy=0.7 + index / 10)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers=_headers(["viewer", "operator"]),
    ) as http:
        report = await _mcp(http, "get_training_supervision_report", {"supervision_id": ident})
        assert report["supervision"]["state"] == "completed"
        assert len(report["comparison"]) == 3
        assert report["activation_performed"] is False
        assert context.config.training.active_model_id == env.model
        # The REST boundary applies the same role restriction as tool visibility.
        for path in [
            f"/api/models/{env.model}/activate",
            "/api/models/deactivate",
            "/api/training/runs",
            "/api/v1/jobs",
            "/api/v1/jobs/execute",
            "/api/v1/training/supervisions",
        ]:
            assert (await http.post(path, json={})).status_code == 403
        listed = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert "submit_training_experiment" in names
        assert not names & {"activate_model", "start_training_run", "approve_training_supervision"}


async def test_internal_mcp_client_preserves_principal_beneath_tool_layer(context):
    from tailcam.mcp.client import TailcamClient
    from tailcam.mcp.errors import TailcamMcpError
    from tailcam.security.principal import RequestPrincipal, TailCamRole

    principal = RequestPrincipal(
        "agent", None, "tailscale-user", True, frozenset({TailCamRole.VIEWER, TailCamRole.OPERATOR})
    )
    client = TailcamClient.for_app(create_app(context.config, context=context), principal=principal)
    try:
        with pytest.raises(TailcamMcpError) as exc:
            await client.deactivate_model()
        assert exc.value.status_code == 403
    finally:
        await client.aclose()
