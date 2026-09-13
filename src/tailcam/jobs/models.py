"""Versioned job contracts shared by coordinators and local-only executors."""

from __future__ import annotations

import json
import math
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tailcam.storage.models import Admission, Artifact, ContentKind

TaskKind = Literal[
    "live_detection",
    "motion_description",
    "printer_analysis",
    "timelapse_encode",
    "timelapse_interpolate",
    "labeling",
    "training",
    "speech_recognition",
    "conversation",
    "speech_synthesis",
]
TASK_KINDS: tuple[TaskKind, ...] = (
    "live_detection",
    "motion_description",
    "printer_analysis",
    "timelapse_encode",
    "timelapse_interpolate",
    "labeling",
    "training",
    "speech_recognition",
    "conversation",
    "speech_synthesis",
)
RESERVED_TASKS = frozenset({"speech_recognition", "conversation", "speech_synthesis"})
JobState = Literal[
    "queued",
    "leased",
    "running",
    "committing",
    "retry_wait",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
    "deadline_exceeded",
    "waiting_for_worker",
]
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "deadline_exceeded"})
Identifier = str


class JobError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 409) -> None:
        super().__init__(detail)
        self.code, self.detail, self.status_code = code, detail, status_code


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, revalidate_instances="always"
    )


class ArtifactRef(Contract):
    artifact_id: str
    owner_node_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0, le=2**63 - 1)
    slot: str = Field(default="input", min_length=1, max_length=128)

    @field_validator("artifact_id", "owner_node_id")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))

    @classmethod
    def from_artifact(cls, artifact: Artifact, slot: str = "input") -> ArtifactRef:
        return cls(
            artifact_id=artifact.artifact_id,
            owner_node_id=artifact.owner_node_id,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            slot=slot,
        )


class ResourceBudget(Contract):
    cpu_threads: int = Field(default=1, ge=1, le=256)
    cpu_seconds: int = Field(default=3600, ge=1, le=604800)
    memory_bytes: int = Field(default=512 * 1024**2, ge=16 * 1024**2, le=2**50)
    gpu_slots: int = Field(default=0, ge=0, le=1)
    workspace_bytes: int = Field(default=256 * 1024**2, ge=1, le=2**50)
    output_bytes: int = Field(default=128 * 1024**2, ge=1, le=2**50)
    wall_seconds: float = Field(default=3600, gt=0, le=604800, allow_inf_nan=False)
    cancel_grace_seconds: float = Field(default=3, ge=0, le=30, allow_inf_nan=False)
    require_hard_memory_limit: bool = False
    require_hard_workspace_limit: bool = False


class WorkerTarget(Contract):
    node_id: str | None = None
    provider_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    model: str = Field(default="", max_length=256)

    @field_validator("node_id")
    @classmethod
    def uuid_value(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None

    @model_validator(mode="after")
    def has_target(self):
        if self.node_id is None and self.provider_id is None:
            raise ValueError("A node or provider is required")
        return self


class TaskRoute(Contract):
    mode: Literal["manual", "auto"] = "manual"
    target: WorkerTarget | None = None
    approved_node_ids: list[str] = Field(default_factory=list, max_length=32)
    fallback_targets: list[WorkerTarget] = Field(default_factory=list, max_length=8)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)

    @field_validator("approved_node_ids")
    @classmethod
    def uuid_values(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(UUID(value)) for value in values))


class PlacementPolicy(Contract):
    revision: int = Field(default=1, ge=1)
    routes: dict[TaskKind, TaskRoute] = Field(default_factory=dict)


class TaskAvailability(Contract):
    task: TaskKind
    state: Literal["ready", "unchecked", "unavailable", "disabled"] = "unchecked"
    code: str = Field(default="runtime_unchecked", max_length=64)
    detail: str = Field(default="Runtime has not been exercised", max_length=512)


class WorkerInfo(Contract):
    node_id: str
    name: str = Field(default="", max_length=128)
    online: bool = False
    roles: list[str] = Field(default_factory=list)
    tasks: list[TaskAvailability] = Field(default_factory=list)
    queued: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    cpu_threads: int = Field(default=1, ge=0)
    memory_bytes: int = Field(default=0, ge=0)
    workspace_bytes: int = Field(default=0, ge=0)
    gpu_slots: int = Field(default=0, ge=0)
    gpu_observed: bool = False
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("node_id")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))


class ProviderDefinition(Contract):
    provider_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["ollama"] = "ollama"
    base_url: str = Field(max_length=2048)
    model: str = Field(min_length=1, max_length=256)
    tasks: list[TaskKind] = Field(
        default_factory=lambda: cast(list[TaskKind], ["motion_description", "printer_analysis"])
    )
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Provider must use a plain HTTP(S) endpoint without credentials")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid provider port")
        return value.rstrip("/")

    @model_validator(mode="after")
    def supported_tasks(self):
        if not self.tasks or any(
            task not in {"motion_description", "printer_analysis", "labeling"}
            for task in self.tasks
        ):
            raise ValueError("Provider does not implement the selected task")
        return self


class PlacementPlan(Contract):
    task: TaskKind
    policy_revision: int = Field(default=1, ge=1)
    mode: Literal["manual", "auto"] = "manual"
    requested_target: WorkerTarget
    selected_target: WorkerTarget
    fallback_targets: list[WorkerTarget] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="Selected local worker", max_length=512)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)


class OutputSlot(Contract):
    slot: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    kind: ContentKind
    artifact_id: str | None = None
    mime_type: str = Field(default="application/octet-stream", max_length=128)
    admission: Admission | None = None

    @field_validator("artifact_id")
    @classmethod
    def uuid_value(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value else None


class StageSpec(Contract):
    stage_id: str = Field(default="main", min_length=1, max_length=64, pattern=r"^[\w.-]+$")
    task: TaskKind
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=10000)
    depends_on: list[str] = Field(default_factory=list, max_length=32)
    outputs: list[OutputSlot] = Field(default_factory=list, max_length=32)
    placement_plan: PlacementPlan | None = None
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    workspace_admission: Admission | None = None

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, allow_nan=False).encode()) > 65536:
            raise ValueError("Task parameters exceed their limit")
        return value


class JobSpec(Contract):
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    task: TaskKind
    origin_node_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=10000)
    outputs: list[OutputSlot] = Field(default_factory=list, max_length=32)
    stages: list[StageSpec] = Field(default_factory=list, max_length=32)
    placement_plan: PlacementPlan | None = None
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    deadline_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_attempts: int = Field(default=2, ge=1, le=10)
    priority: int = Field(default=50, ge=0, le=100)
    reference: dict[str, str] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        return StageSpec.bounded_parameters(value)

    @field_validator("job_id", "origin_node_id")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))


class SafeError(Contract):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    detail: str = Field(max_length=512)
    retryable: bool = False


class Progress(Contract):
    fraction: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    epoch: int | None = Field(default=None, ge=0)
    message: str = Field(default="", max_length=512)
    metrics: dict[str, float] = Field(default_factory=dict)

    @field_validator("metrics")
    @classmethod
    def bounded_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > 128 or any(
            len(key) > 128 or not math.isfinite(v) for key, v in value.items()
        ):
            raise ValueError("Progress metrics must be finite and bounded")
        return value


class Lease(Contract):
    job_id: str
    stage_id: str
    attempt_id: str
    fence_token: str
    worker_session: str
    worker_node_id: str
    expires_at: float
    deadline_at: float
    attempt: int
    spec: StageSpec


class PreparedOutput(Contract):
    slot: str
    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=2**63 - 1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        if (
            value.startswith("/")
            or "\\" in value
            or ":" in value
            or any(p in {"", ".", ".."} for p in value.split("/"))
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Output path must be a safe relative file")
        return value


class ResultManifest(Contract):
    outputs: list[PreparedOutput] = Field(default_factory=list, max_length=32)
    result: dict[str, Any] = Field(default_factory=dict)

    @field_validator("result")
    @classmethod
    def bounded_result(cls, value: dict[str, Any]) -> dict[str, Any]:
        return StageSpec.bounded_parameters(value)


class PublicationPermit(Contract):
    job_id: str
    origin_node_id: str | None = None
    stage_id: str
    attempt_id: str
    fence_token: str
    permit_id: str
    manifest: ResultManifest
    slots: list[OutputSlot]


class StageRecord(Contract):
    stage_id: str
    task: TaskKind
    state: JobState = "queued"
    attempt: int = 0
    worker_node_id: str | None = None
    placement_plan: PlacementPlan | None = None
    progress: Progress = Field(default_factory=Progress)
    heartbeat_at: float | None = None
    started_at: float | None = None
    ended_at: float | None = None
    error: SafeError | None = None
    outputs: list[ArtifactRef] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class JobRecord(Contract):
    job_id: str
    coordinator_node_id: str
    origin_node_id: str
    task: TaskKind
    state: JobState = "queued"
    revision: int = 1
    created_at: float
    updated_at: float
    deadline_at: float
    started_at: float | None = None
    ended_at: float | None = None
    requested_target: WorkerTarget | None = None
    actual_target: WorkerTarget | None = None
    priority: int
    stages: list[StageRecord]
    error: SafeError | None = None
    cancel_requested: bool = False
    allowed_actions: list[str] = Field(default_factory=lambda: ["cancel"])
    reference: dict[str, str] = Field(default_factory=dict)
