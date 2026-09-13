"""Wire contracts. Filesystem paths belong only to a node's local location records."""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ContentKind = Literal[
    "recording",
    "snapshot",
    "thumbnail",
    "timelapse_frame",
    "timelapse_video",
    "timelapse_smooth",
    "analysis_evidence",
    "training_sample",
    "annotation",
    "model_output",
    "export",
]
CONTENT_KINDS = (
    "recording",
    "snapshot",
    "thumbnail",
    "timelapse_frame",
    "timelapse_video",
    "timelapse_smooth",
    "analysis_evidence",
    "training_sample",
    "annotation",
    "model_output",
    "export",
)
OutagePolicy = Literal["destination_required", "local_spool", "secondary"]
ArtifactState = Literal["pending_transfer", "committed", "replicated", "failed", "deleted"]
MAX_INTEGER = 2**63 - 1
MAX_CHUNK_BYTES = 4 * 1024 * 1024


class StorageError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        revalidate_instances="always",
    )


class DestinationRef(Contract):
    node_id: str
    location_id: str | None = None

    @field_validator("node_id", "location_id")
    @classmethod
    def uuid_value(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None


class RetentionPolicy(Contract):
    enabled: bool = False
    max_age_seconds: int = Field(default=0, ge=0, le=MAX_INTEGER)
    min_replicas: int = Field(default=1, ge=1, le=10)
    protect: bool = False


class PolicyOverride(Contract):
    origin_node_id: str | None = None
    camera_id: str | None = Field(default=None, min_length=1, max_length=256)
    content_kind: ContentKind | None = None
    destination: DestinationRef

    @model_validator(mode="after")
    def selector(self) -> PolicyOverride:
        if self.camera_id and not self.origin_node_id:
            raise ValueError("camera overrides require origin_node_id")
        if self.origin_node_id:
            self.__dict__["origin_node_id"] = str(UUID(self.origin_node_id))
        if not self.camera_id and not self.content_kind:
            raise ValueError("an override requires camera_id or content_kind")
        return self


class StoragePolicy(Contract):
    revision: int = Field(default=1, ge=1, le=MAX_INTEGER)
    default_destination: DestinationRef
    overrides: list[PolicyOverride] = Field(default_factory=list, max_length=256)
    outage_policy: OutagePolicy = "destination_required"
    secondary_destination: DestinationRef | None = None
    zero_local_media: bool = False
    spool_max_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    spool_max_age_seconds: int = Field(default=86400, ge=1, le=MAX_INTEGER)
    workspace_max_bytes: int = Field(default=1024**3, ge=0, le=MAX_INTEGER)
    artifact_max_bytes: int = Field(default=16 * 1024**3, ge=1, le=MAX_INTEGER)
    source_cleanup: Literal["after_primary_commit", "retain"] = "after_primary_commit"
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)

    @model_validator(mode="after")
    def consistent(self) -> StoragePolicy:
        if self.outage_policy == "secondary" and self.secondary_destination is None:
            raise ValueError("secondary outage policy requires a destination")
        if self.outage_policy == "local_spool" and self.spool_max_bytes == 0:
            raise ValueError("local spool policy requires a positive spool budget")
        if self.zero_local_media and self.outage_policy == "local_spool":
            raise ValueError("zero local media cannot use a local spool")
        selectors = [(x.origin_node_id, x.camera_id, x.content_kind) for x in self.overrides]
        if len(set(selectors)) != len(selectors):
            raise ValueError("duplicate storage override")
        return self


class StorageLocation(Contract):
    location_id: str
    node_id: str
    label: str = Field(default="", max_length=128)
    path: str
    marker: str
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    created_at: float = Field(ge=0, allow_inf_nan=False)
    quota_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    reserve_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    is_default: bool = False
    state: Literal["ready", "missing", "changed", "unwritable"] = "ready"
    used_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    reserved_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    free_bytes: int | None = Field(default=None, ge=0, le=MAX_INTEGER)
    allocatable_bytes: int | None = Field(default=None, ge=0, le=MAX_INTEGER)

    @field_validator("location_id", "node_id", "marker")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))


class ArtifactRef(Contract):
    artifact_id: str
    owner_node_id: str


class Artifact(Contract):
    artifact_id: str
    owner_node_id: str
    origin_node_id: str
    camera_id: str = Field(default="", max_length=256)
    kind: ContentKind
    mime_type: str = Field(
        default="application/octet-stream",
        max_length=256,
        pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$",
    )
    size_bytes: int = Field(ge=0, le=MAX_INTEGER)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: float = Field(ge=0, allow_inf_nan=False)
    updated_at: float = Field(ge=0, allow_inf_nan=False)
    state: ArtifactState = "committed"
    location_id: str | None = None
    requested_destination: DestinationRef
    policy_revision: int = Field(ge=1)
    parent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    replicas: list[DestinationRef] = Field(default_factory=list, max_length=10)
    owner_online: bool | None = None
    last_seen: float | None = None

    @field_validator("artifact_id", "owner_node_id", "origin_node_id", "location_id", "parent_id")
    @classmethod
    def uuid_value(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, allow_nan=False).encode()) > 65536:
            raise ValueError("artifact metadata exceeds 64 KiB")
        return value


class TransferManifest(Contract):
    artifact: Artifact
    destination: DestinationRef
    idempotency_key: str = Field(min_length=1, max_length=256)
    retain_source: bool = False


class ArtifactPin(Contract):
    pin_id: str
    coordinator_node_id: str
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0, le=MAX_INTEGER)

    @field_validator("pin_id", "coordinator_node_id")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))


class Transfer(Contract):
    transfer_id: str
    artifact_id: str
    location_id: str
    offset: int = Field(default=0, ge=0, le=MAX_INTEGER)
    size_bytes: int = Field(ge=0, le=MAX_INTEGER)
    state: Literal["receiving", "verifying", "committed", "failed", "cancelled"]
    created_at: float = Field(ge=0, allow_inf_nan=False)
    updated_at: float = Field(ge=0, allow_inf_nan=False)
    error_code: str | None = None
    direction: Literal["receiver", "outbound"] = "receiver"
    requested_destination: DestinationRef | None = None
    actual_owner_node_id: str | None = None

    @field_validator("transfer_id", "artifact_id", "location_id")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))


class Admission(Contract):
    allowed: bool = True
    kind: ContentKind
    destination: DestinationRef
    requested_destination: DestinationRef
    policy_revision: int
    outage_policy: OutagePolicy
    max_bytes: int
    spooled: bool = False
    workspace_allowed: bool = False
    workspace_max_bytes: int = 0
    spool_max_bytes: int = 0
    spool_max_age_seconds: int = 86400
    source_cleanup: Literal["after_primary_commit", "retain"] = "after_primary_commit"
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)
    secondary_destination: DestinationRef | None = None
