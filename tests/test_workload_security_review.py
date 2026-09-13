"""Regression boundaries from the independent workload/security review."""

import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tailcam.jobs.models import JobSpec, WorkerTarget
from tailcam.mcp.server import McpServer
from tailcam.security.principal import RequestPrincipal, TailCamRole
from tailcam.web.routes_workloads_v1 import retry_job


def test_operator_cannot_retry_a_mixed_job_with_a_training_stage():
    record = SimpleNamespace(
        task="printer_analysis", reference={}, stages=[SimpleNamespace(task="training")]
    )
    ctx = SimpleNamespace(
        jobs=SimpleNamespace(get=lambda _: record),
        training=SimpleNamespace(project_job=lambda _: None),
    )
    operator = RequestPrincipal(
        "agent", None, "tailscale-node", True, frozenset({TailCamRole.VIEWER, TailCamRole.OPERATOR})
    )
    with pytest.raises(HTTPException) as error:
        retry_job(uuid4(), ctx=ctx, principal=operator)
    assert error.value.status_code == 403


@pytest.mark.anyio
async def test_unexpected_mcp_exception_never_exposes_private_text():
    server = object.__new__(McpServer)

    async def fail(method, params):
        raise RuntimeError("https://user:secret@private.invalid/internal")

    server._dispatch = fail
    result = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert result["error"]["message"] == "internal error"
    assert "secret" not in str(result)


@pytest.mark.parametrize(
    "remote,provider,ready", [(True, False, False), (False, True, False), (False, False, True)]
)
def test_only_local_nonprovider_stage_execution_proves_worker_runtime(
    context, remote, provider, ready
):
    job = context.jobs.submit(
        JobSpec(task="printer_analysis", origin_node_id=context.node_id), idempotency_key="recent"
    )
    stage = job.stages[0]
    stage.state = job.state = "succeeded"
    stage.worker_node_id = str(uuid4()) if remote else context.node_id
    stage.ended_at = job.updated_at = time.time()
    stage.result = {"device": "cuda"}
    if provider:
        stage.placement_plan.selected_target = WorkerTarget(
            node_id=context.node_id, provider_id="ollama"
        )
    with context.jobs.journal.transaction() as conn:
        context.jobs.journal.save(job, conn)
    info = context.workload_peers.local()
    task = next(item for item in info.tasks if item.task == "printer_analysis")
    assert (task.state == "ready") is ready
    assert info.gpu_observed is ready
