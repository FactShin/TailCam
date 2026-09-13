"""Minimal MCP host contract for already-approved, finite training experiments."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from tailcam.jobs.models import Contract
from tailcam.mcp import errors
from tailcam.mcp.errors import TailcamMcpError
from tailcam.mcp.tools import Tool, ToolResult
from tailcam.security.principal import TailCamRole
from tailcam.training.supervisor_models import AgentHeartbeat, ExperimentRequest, SupervisionCreate


class SupervisionID(Contract):
    supervision_id: UUID


class ExperimentInput(SupervisionID):
    experiment: ExperimentRequest


class HeartbeatInput(SupervisionID):
    heartbeat: AgentHeartbeat


class FinishInput(SupervisionID):
    reason: str = Field(min_length=1, max_length=1024)


class EventsInput(SupervisionID):
    after: int = Field(default=0, ge=0)


class ListInput(Contract):
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class DatasetInput(Contract):
    dataset_id: int = Field(ge=1)


def _validated(model: type[BaseModel], args: dict[str, Any]):
    try:
        return model.model_validate(args)
    except ValidationError:
        raise TailcamMcpError(
            errors.INVALID_REQUEST, "Invalid supervision fields or limits"
        ) from None


def build_supervisor_tools() -> list[Tool]:
    async def revision(ctx, args):
        body = _validated(DatasetInput, args)
        data = await ctx.client.dataset_revision(body.dataset_id)
        return ToolResult("Current dataset content revision and permitted-scope candidates", data)

    async def listing(ctx, args):
        body = _validated(ListInput, args)
        data = await ctx.client.supervisions(cursor=body.cursor, limit=body.limit)
        return ToolResult("Approved training supervision records", data)

    async def approve(ctx, args):
        body = _validated(SupervisionCreate, args)
        data = await ctx.client.approve_supervision(body.model_dump(mode="json"))
        return ToolResult("Finite training policy approved; no experiment has started", data)

    async def inspect(ctx, args):
        body = _validated(SupervisionID, args)
        data = await ctx.client.supervision(str(body.supervision_id))
        return ToolResult(
            f"Supervision is {data['state']}; read this before submitting new work", data
        )

    async def experiment(ctx, args):
        body = _validated(ExperimentInput, args)
        data = await ctx.client.supervision_experiment(
            str(body.supervision_id),
            body.experiment.model_dump(mode="json"),
        )
        return ToolResult("Experiment reservation recorded under the approved policy", data)

    async def heartbeat(ctx, args):
        body = _validated(HeartbeatInput, args)
        data = await ctx.client.supervision_heartbeat(
            str(body.supervision_id),
            body.heartbeat.model_dump(mode="json"),
        )
        return ToolResult("Agent heartbeat recorded separately from worker progress", data)

    async def stop(ctx, args):
        body = _validated(SupervisionID, args)
        data = await ctx.client.stop_supervision(str(body.supervision_id))
        return ToolResult(f"Supervision is {data['state']}; stop_requested is not stopped", data)

    async def finish(ctx, args):
        body = _validated(FinishInput, args)
        data = await ctx.client.finish_supervision(str(body.supervision_id), body.reason)
        return ToolResult("Supervision finished without activating a model", data)

    async def report(ctx, args):
        body = _validated(SupervisionID, args)
        data = await ctx.client.supervision_report(str(body.supervision_id))
        return ToolResult(
            "Experiment evidence and comparison; candidate ranking is not deployment approval", data
        )

    async def events(ctx, args):
        body = _validated(EventsInput, args)
        data = await ctx.client.supervision_events(str(body.supervision_id), body.after)
        return ToolResult(
            "Stable event IDs for authenticated polling and duplicate suppression", data
        )

    definitions: list[tuple[str, str, type[BaseModel], Any, str, TailCamRole, bool]] = [
        (
            "get_training_dataset_revision",
            "Review dataset revision",
            DatasetInput,
            revision,
            "Inspect current content hash, task, cameras and classes before approval.",
            TailCamRole.VIEWER,
            False,
        ),
        (
            "list_training_supervisions",
            "List training approvals",
            ListInput,
            listing,
            "List durable supervision records and remaining approved budgets.",
            TailCamRole.VIEWER,
            False,
        ),
        (
            "approve_training_supervision",
            "Approve bounded experiments",
            SupervisionCreate,
            approve,
            "Approve a finite objective and policy; this does not launch work or activate models.",
            TailCamRole.ADMIN,
            True,
        ),
        (
            "get_training_supervision",
            "Inspect training supervision",
            SupervisionID,
            inspect,
            "Reconnect by reading the saved state before planning another experiment.",
            TailCamRole.VIEWER,
            False,
        ),
        (
            "submit_training_experiment",
            "Submit approved experiment",
            ExperimentInput,
            experiment,
            "Reserve one approved experiment. "
            "Persist and reuse its idempotency key after lost replies.",
            TailCamRole.OPERATOR,
            True,
        ),
        (
            "heartbeat_training_supervisor",
            "Report supervisor availability",
            HeartbeatInput,
            heartbeat,
            "Report the external agent session, decision and next poll. "
            "Does not keep the agent awake.",
            TailCamRole.OPERATOR,
            True,
        ),
        (
            "stop_training_supervision",
            "Stop supervision",
            SupervisionID,
            stop,
            "Request worker termination and prevent further experiments; poll until stopped.",
            TailCamRole.OPERATOR,
            True,
        ),
        (
            "finish_training_supervision",
            "Finish supervision",
            FinishInput,
            finish,
            "Finish an idle supervision with a reason; the active model remains unchanged.",
            TailCamRole.OPERATOR,
            True,
        ),
        (
            "get_training_supervision_report",
            "Compare experiment evidence",
            SupervisionID,
            report,
            "Read every run, setting, metric, artifact and limitation; no automatic promotion.",
            TailCamRole.VIEWER,
            False,
        ),
        (
            "list_training_supervision_events",
            "Poll supervision events",
            EventsInput,
            events,
            "Persist the returned cursor; replay is safe and event IDs suppress duplicates.",
            TailCamRole.VIEWER,
            False,
        ),
    ]
    return [
        Tool(
            name,
            title,
            description,
            schema.model_json_schema(),
            handler,
            min_role=role,
            write=write,
        )
        for name, title, schema, handler, description, role, write in definitions
    ]
