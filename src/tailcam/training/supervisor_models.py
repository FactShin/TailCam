"""Approved, bounded experiment contracts; model activation is deliberately absent."""

from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from tailcam.jobs.models import Contract, JobRecord, ResourceBudget

SupervisionAction = Literal["experiment", "inspect", "stop", "finish"]


class IntegerRange(Contract):
    minimum: int = Field(ge=1, le=65536)
    maximum: int = Field(ge=1, le=65536)

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum > self.maximum:
            raise ValueError("Range minimum exceeds maximum")
        return self


class SuccessCriterion(Contract):
    metric: str = Field(min_length=1, max_length=128, pattern=r"^[\w./()-]+$")
    direction: Literal["maximize", "minimize"] = "maximize"
    threshold: float = Field(allow_inf_nan=False)


class TrainingObjective(Contract):
    name: str = Field(min_length=1, max_length=160)
    task: Literal["classification", "detection"]
    dataset_id: int = Field(ge=1)
    dataset_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    camera_ids: list[str] = Field(min_length=1, max_length=128)
    classes: list[str] = Field(min_length=1, max_length=256)
    success_criteria: list[SuccessCriterion] = Field(min_length=1, max_length=16)
    evaluation_reference: str = Field(min_length=1, max_length=256)

    @field_validator("camera_ids", "classes")
    @classmethod
    def bounded_names(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 256 for value in values):
            raise ValueError("A scope entry is empty or too long")
        return list(dict.fromkeys(values))


class SupervisionPolicy(Contract):
    allowed_model_ids: list[int] = Field(min_length=1, max_length=32)
    allowed_worker_node_ids: list[str] = Field(min_length=1, max_length=32)
    epochs: IntegerRange = Field(default_factory=lambda: IntegerRange(minimum=1, maximum=10))
    image_size: IntegerRange = Field(default_factory=lambda: IntegerRange(minimum=224, maximum=640))
    allowed_seeds: list[int] = Field(default_factory=lambda: [0], min_length=1, max_length=32)
    max_experiments: int = Field(default=3, ge=1, le=100)
    total_wall_seconds: float = Field(default=10800, gt=0, le=604800, allow_inf_nan=False)
    experiment_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    max_attempts: int = Field(default=1, ge=1, le=3)
    agent_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    permitted_actions: list[SupervisionAction] = Field(
        default_factory=lambda: cast(
            list[SupervisionAction], ["experiment", "inspect", "stop", "finish"]
        ),
        max_length=4,
    )
    activation_enabled: Literal[False] = False

    @field_validator("allowed_model_ids")
    @classmethod
    def model_ids(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("Models must be registered model identifiers")
        return list(dict.fromkeys(values))

    @field_validator("allowed_seeds")
    @classmethod
    def seeds(cls, values: list[int]) -> list[int]:
        if any(not 0 <= value <= 2**31 - 1 for value in values):
            raise ValueError("Seed is out of range")
        return list(dict.fromkeys(values))

    @field_validator("allowed_worker_node_ids")
    @classmethod
    def worker_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(UUID(value)) for value in values))

    @model_validator(mode="after")
    def sufficient_budget(self):
        if self.experiment_budget.wall_seconds > self.total_wall_seconds:
            raise ValueError("Total budget cannot admit one experiment")
        if "stop" not in self.permitted_actions or "inspect" not in self.permitted_actions:
            raise ValueError("Inspection and stop must remain available")
        return self


class SupervisionCreate(Contract):
    objective: TrainingObjective
    policy: SupervisionPolicy


class ExperimentRequest(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[\w.-]+$")
    base_model_id: int = Field(ge=1)
    worker_node_id: str
    epochs: int = Field(ge=1, le=65536)
    image_size: int = Field(ge=32, le=65536)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    reason: str = Field(min_length=1, max_length=1024)

    @field_validator("worker_node_id")
    @classmethod
    def worker_id(cls, value: str) -> str:
        return str(UUID(value))


class AgentHeartbeat(Contract):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[\w.-]+$")
    decision: Literal["inspect", "wait", "experiment", "stop", "finish"] = "inspect"
    reason: str = Field(default="", max_length=1024)
    next_check_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class ExperimentRecord(Contract):
    experiment_id: str
    job_id: str
    request: ExperimentRequest
    created_at: float
    reserved_wall_seconds: float
    state: str = "reserved"
    job: JobRecord | None = None
    error_code: str | None = None


class SupervisionRecord(Contract):
    supervision_id: str
    node_id: str
    owner: str
    revision: int = 1
    objective: TrainingObjective
    policy: SupervisionPolicy
    created_at: float
    updated_at: float
    state: Literal[
        "active", "stop_requested", "stopped", "completed", "budget_exhausted", "failed"
    ] = "active"
    agent_session_id: str | None = None
    agent_heartbeat_at: float | None = None
    agent_connected: bool = False
    last_decision: str = "approved"
    reason: str = "Awaiting an experiment within the approved policy"
    next_check_at: float | None = None
    current_job_id: str | None = None
    remaining_experiments: int
    remaining_wall_seconds: float
    experiments: list[ExperimentRecord] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
