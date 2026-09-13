"""Typed workload, job and approved training APIs with bounded request bodies."""

from __future__ import annotations

from typing import TypeVar
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from tailcam.jobs.models import (
    Contract,
    JobSpec,
    JobState,
    PlacementPolicy,
    ProviderDefinition,
    TaskKind,
)
from tailcam.management.audit import AuditLog
from tailcam.security.principal import RequestPrincipal, TailCamRole
from tailcam.training.supervisor_models import (
    AgentHeartbeat,
    ExperimentRequest,
    SupervisionCreate,
)
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.routes_node_v1 import require_admin
from tailcam.web.routes_storage_v1 import _bounded_body, require_operator, require_viewer

router = APIRouter(prefix="/api/v1")
Model = TypeVar("Model", bound=BaseModel)


async def _body(request: Request, model: type[Model]) -> Model:
    raw = await _bounded_body(request, 1024 * 1024)
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(
            422, "Invalid workload request; check the documented fields and limits"
        ) from None


def _audit(ctx, principal, action, target):
    AuditLog(ctx.store).record(
        actor=principal.actor,
        source=principal.source,
        action=action,
        target=target,
        result="success",
        detail="Accepted through the approved workload policy",
    )


class PlacementUpdate(Contract):
    expected_revision: int = Field(ge=1)
    policy: PlacementPolicy


class JobSubmission(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[\w.-]+$")
    spec: JobSpec


class WorkerAssignment(Contract):
    coordinator_node_id: UUID
    spec: JobSpec


class FinishRequest(Contract):
    reason: str = Field(min_length=1, max_length=1024)


@router.get("/workloads/policy")
def policy(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    return {"source_node_id": ctx.node_id, "policy": ctx.jobs.get_policy()}


@router.patch("/workloads/policy")
async def update_policy(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    body = await _body(request, PlacementUpdate)
    saved = await anyio.to_thread.run_sync(
        lambda: ctx.jobs.set_policy(body.policy, expected_revision=body.expected_revision)
    )
    _audit(ctx, principal, "workloads.policy", ctx.node_id)
    return {"source_node_id": ctx.node_id, "policy": saved}


@router.get("/workloads/workers")
async def workers(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    await ctx.cluster.peers()
    return {"items": await anyio.to_thread.run_sync(ctx.jobs.workers)}


@router.get("/workloads/worker")
def local_worker(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    return ctx.workload_peers.local()


@router.post("/workloads/live/execute")
async def execute_live(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    from tailcam.workloads.live import LiveRequest

    raw = await _bounded_body(request, 17 * 1024**2)
    try:
        body = LiveRequest.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Invalid live workload request") from None
    coordinator = str(body.coordinator_node_id)
    if coordinator != ctx.node_id:
        approved = await anyio.to_thread.run_sync(lambda: ctx.storage_peers.resolve(coordinator))
        if approved is None:
            if ctx.storage_peers.is_approved_identity(coordinator):
                raise HTTPException(503, "Approved coordinator is temporarily unavailable")
            raise HTTPException(403, "The coordinator must be an approved, bound TailCam peer")
    return await anyio.to_thread.run_sync(lambda: ctx.workloads.execute_live(body))


@router.get("/workloads/providers")
def providers(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    return {"items": ctx.jobs.providers()}


@router.post("/workloads/providers")
async def register_provider(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    body = await _body(request, ProviderDefinition)
    result = await anyio.to_thread.run_sync(lambda: ctx.jobs.placement.register_provider(body))
    _audit(ctx, principal, "workloads.provider.register", body.provider_id)
    return result


@router.delete("/workloads/providers/{provider_id}")
def delete_provider(
    provider_id: str,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    result = ctx.jobs.placement.delete_provider(provider_id)
    _audit(ctx, principal, "workloads.provider.delete", provider_id)
    return {"deleted": result}


@router.get("/jobs")
def jobs(
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
    task: TaskKind | None = None,
    state: JobState | None = None,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    items = ctx.jobs.list(task=task, state=state, offset=cursor, limit=limit)
    return {"items": items, "next_cursor": str(cursor + limit) if len(items) == limit else None}


@router.post("/jobs")
async def submit_job(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    body = await _body(request, JobSubmission)
    if body.spec.reference.get("supervision_id"):
        raise HTTPException(403, "Submit approved experiments through their supervision record")
    result = await anyio.to_thread.run_sync(
        lambda: ctx.jobs.submit(
            body.spec,
            idempotency_key=body.idempotency_key,
            principal_scope=principal.actor,
        )
    )
    _audit(ctx, principal, "jobs.submit", result.job_id)
    return result


def _job(ctx, job_id):
    record = ctx.jobs.get(str(job_id))
    if record is None:
        raise HTTPException(404, "Job was not found")
    ctx.training.project_job(record)
    return record


@router.post("/jobs/execute")
async def execute_job(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    body = await _body(request, WorkerAssignment)
    coordinator = str(body.coordinator_node_id)
    if coordinator == ctx.node_id:
        raise HTTPException(409, "A delegated assignment must name another coordinator")
    approved = await anyio.to_thread.run_sync(lambda: ctx.storage_peers.resolve(coordinator))
    if approved is None:
        if ctx.storage_peers.is_approved_identity(coordinator):
            raise HTTPException(503, "Approved coordinator is temporarily unavailable")
        raise HTTPException(403, "The coordinator must be an approved, bound TailCam peer")
    result = await anyio.to_thread.run_sync(
        lambda: ctx.jobs.accept_remote(body.spec, coordinator_node_id=coordinator)
    )
    _audit(ctx, principal, "jobs.execute", result.job_id)
    return result


@router.get("/jobs/{job_id}")
def get_job(job_id: UUID, ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    return _job(ctx, job_id)


@router.get("/jobs/{job_id}/events")
def job_events(
    job_id: UUID,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    _job(ctx, job_id)
    return ctx.jobs.events(str(job_id), after=after, limit=limit)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: UUID,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_operator),
):
    _job(ctx, job_id)
    result = ctx.jobs.request_cancel(str(job_id))
    _audit(ctx, principal, "jobs.cancel", str(job_id))
    return result


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: UUID,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_operator),
):
    record = _job(ctx, job_id)
    if record.reference.get("supervision_id"):
        raise HTTPException(403, "Supervised retries use the original approved attempt budget")
    if (
        record.task == "training" or any(stage.task == "training" for stage in record.stages)
    ) and TailCamRole.ADMIN not in principal.roles:
        raise HTTPException(403, "Unsupervised training requires administrator approval")
    result = ctx.jobs.retry(str(job_id))
    _audit(ctx, principal, "jobs.retry", str(job_id))
    return result


@router.get("/training/datasets/{dataset_id}/revision")
def dataset_revision(
    dataset_id: int, ctx: AppContext = Depends(get_context), _=Depends(require_viewer)
):
    return ctx.training.dataset_review(dataset_id)


@router.get("/training/supervisions")
def supervisions(
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    return ctx.supervisor.list(cursor=cursor, limit=limit)


@router.post("/training/supervisions")
async def approve_supervision(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    body = await _body(request, SupervisionCreate)
    result = await anyio.to_thread.run_sync(
        lambda: ctx.supervisor.create(body, owner=principal.actor)
    )
    _audit(ctx, principal, "supervision.approve", result.supervision_id)
    return result


@router.get("/training/supervisions/{supervision_id}")
def supervision(
    supervision_id: UUID, ctx: AppContext = Depends(get_context), _=Depends(require_viewer)
):
    return ctx.supervisor.get(str(supervision_id))


@router.post("/training/supervisions/{supervision_id}/experiments")
async def experiment(
    supervision_id: UUID,
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_operator),
):
    body = await _body(request, ExperimentRequest)
    result = await anyio.to_thread.run_sync(
        lambda: ctx.supervisor.experiment(str(supervision_id), body)
    )
    _audit(ctx, principal, "supervision.experiment", str(supervision_id))
    return result


@router.post("/training/supervisions/{supervision_id}/heartbeat")
async def heartbeat(
    supervision_id: UUID,
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_operator),
):
    body = await _body(request, AgentHeartbeat)
    return await anyio.to_thread.run_sync(
        lambda: ctx.supervisor.heartbeat(str(supervision_id), body)
    )


@router.post("/training/supervisions/{supervision_id}/stop")
def stop_supervision(
    supervision_id: UUID,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_operator),
):
    result = ctx.supervisor.stop(str(supervision_id))
    _audit(ctx, principal, "supervision.stop", str(supervision_id))
    return result


@router.post("/training/supervisions/{supervision_id}/finish")
async def finish_supervision(
    supervision_id: UUID,
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_operator),
):
    body = await _body(request, FinishRequest)
    result = await anyio.to_thread.run_sync(
        lambda: ctx.supervisor.finish(str(supervision_id), reason=body.reason)
    )
    _audit(ctx, principal, "supervision.finish", str(supervision_id))
    return result


@router.get("/training/supervisions/{supervision_id}/report")
def supervision_report(
    supervision_id: UUID, ctx: AppContext = Depends(get_context), _=Depends(require_viewer)
):
    return ctx.supervisor.report(str(supervision_id))


@router.get("/training/supervisions/{supervision_id}/events")
def supervision_events(
    supervision_id: UUID,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
):
    return ctx.supervisor.events(str(supervision_id), after=after, limit=limit)
