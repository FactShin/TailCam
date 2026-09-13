"""Bounded scheduling, lease fencing and durable staged publication."""

from __future__ import annotations

import builtins
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from tailcam.jobs.models import (
    RESERVED_TASKS,
    TERMINAL,
    ArtifactRef,
    JobError,
    JobRecord,
    JobSpec,
    Lease,
    Progress,
    ProviderDefinition,
    PublicationPermit,
    ResourceBudget,
    ResultManifest,
    SafeError,
    StageRecord,
    StageSpec,
)
from tailcam.jobs.placement import PlacementService
from tailcam.jobs.store import JobStore
from tailcam.node import RoleDisabledError
from tailcam.storage.models import ArtifactPin, StorageError

_ACTIVE = ("leased", "running", "committing", "cancel_requested")
_RETRYABLE = frozenset(
    {"worker_lost", "worker_unavailable", "owner_unavailable", "transfer_timeout"}
)


class JobService:
    def __init__(
        self,
        config,
        store,
        node_id: str,
        storage_service=None,
        *,
        placement=None,
        executor=None,
        role_check=None,
        clock: Callable[[], float] = time.time,
        transport=None,
    ):
        self.config, self.node_id, self.storage = config, str(UUID(node_id)), storage_service
        self.journal = JobStore(store)
        self.placement = placement or PlacementService(config, self.journal, self.node_id)
        self.executor, self.transport = executor, transport
        self.clock = clock
        self.active_roles = frozenset(config.node.roles)
        self.role_check = role_check or self._require_role
        self.worker_session = str(uuid4())
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._running: dict[str, threading.Thread] = {}
        self._publication_retry_after: dict[str, float] = {}
        self._live_budgets: dict[str, ResourceBudget] = {}

    def _require_role(self, role):
        if role not in self.active_roles:
            raise RoleDisabledError(role)

    def get(self, job_id: str) -> JobRecord | None:
        return self.journal.get(job_id)

    def list(self, **kwargs) -> builtins.list[JobRecord]:
        return self.journal.list(**kwargs)

    def events(self, job_id: str, **kwargs):
        return self.journal.events(job_id, **kwargs)

    def get_policy(self):
        return self.placement.get_policy()

    def set_policy(self, policy, *, expected_revision):
        return self.placement.set_policy(policy, expected_revision=expected_revision)

    def providers(self):
        return self.placement.providers()

    def workers(self):
        return self.placement.workers()

    def reserve_live(self, session_id: str, budget: ResourceBudget) -> None:
        self.role_check("analysis")
        budget = ResourceBudget.model_validate(budget).model_copy(deep=True)
        if not 1 <= len(session_id) <= 256:
            raise JobError("invalid_session", "Live session identity is invalid.", 422)
        self._validate_budget(budget)
        with self.journal.transaction() as conn:
            with self._lock:
                existing = self._live_budgets.get(session_id)
            if existing is not None:
                if existing != budget:
                    raise JobError("session_conflict", "Live session budget is already frozen.")
                return
            if not self._fits(budget, "live_detection", conn):
                raise JobError("worker_busy", "Worker capacity is reserved by active work.", 429)
            with self._lock:
                self._live_budgets[session_id] = budget

    def release_live(self, session_id: str) -> None:
        with self.journal.transaction():
            with self._lock:
                self._live_budgets.pop(session_id, None)
        self._wake.set()

    def live_usage(self) -> builtins.list[ResourceBudget]:
        with self._lock:
            return [budget.model_copy(deep=True) for budget in self._live_budgets.values()]

    def accept_remote(self, spec: JobSpec | dict, *, coordinator_node_id: str) -> JobRecord:
        spec = JobSpec.model_validate(spec).model_copy(deep=True)
        if not spec.stages or any(
            stage.placement_plan is None
            or stage.placement_plan.selected_target.node_id != self.node_id
            for stage in spec.stages
        ):
            raise JobError(
                "wrong_worker", "Execution endpoint accepts only stages assigned to this node.", 422
            )
        if spec.reference.get("supervision_id"):
            raise JobError(
                "invalid_assignment", "Supervised authorization belongs to its coordinator.", 422
            )
        return self.submit(
            spec,
            idempotency_key="assignment:" + spec.job_id,
            principal_scope="node:" + str(UUID(coordinator_node_id)),
            coordinator_node_id=str(UUID(coordinator_node_id)),
        )

    def spec(self, job_id: str) -> JobSpec:
        spec = self.journal.spec(job_id)
        if spec is None:
            raise JobError("job_missing", "Job does not exist.", 404)
        return spec

    @staticmethod
    def _supervision_allowed(spec: JobSpec, conn) -> bool:
        supervision = spec.reference.get("supervision_id")
        if not supervision:
            return True
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"training_supervisions", "training_experiments"}.issubset(tables):
            return False
        return bool(
            conn.execute(
                "SELECT 1 FROM training_supervisions s "
                "JOIN training_experiments e ON e.supervision=s.id "
                "WHERE s.id=? AND s.state='active' AND e.job=?",
                (supervision, spec.job_id),
            ).fetchone()
        )

    def submit(
        self,
        spec: JobSpec | dict,
        *,
        idempotency_key: str,
        principal_scope: str = "local",
        coordinator_node_id: str | None = None,
    ) -> JobRecord:
        spec = JobSpec.model_validate(spec)
        if self.storage is None:
            return self._submit(
                spec,
                idempotency_key=idempotency_key,
                principal_scope=principal_scope,
                coordinator_node_id=coordinator_node_id,
            )
        # Serialize same-UUID admissions while remote holds are acquired without an
        # open SQLite transaction. A losing request cannot release the winner's holds.
        lock_id = str(uuid5(UUID(self.node_id), "job-admission:" + spec.job_id))
        with self.storage.pins.guard(lock_id):
            return self._submit(
                spec,
                idempotency_key=idempotency_key,
                principal_scope=principal_scope,
                coordinator_node_id=coordinator_node_id,
            )

    def _submit(
        self,
        spec: JobSpec | dict,
        *,
        idempotency_key: str,
        principal_scope: str = "local",
        coordinator_node_id: str | None = None,
    ) -> JobRecord:
        spec = JobSpec.model_validate(spec).model_copy(deep=True)
        if not 1 <= len(idempotency_key) <= 128 or not 1 <= len(principal_scope) <= 256:
            raise JobError("invalid_idempotency", "Invalid idempotency identity.", 422)
        # The generated job UUID and server-derived route/deadline are not client request identity.
        canonical = spec.model_dump(mode="json", exclude={"job_id"})
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        with self.journal.transaction() as conn:
            old = conn.execute(
                "SELECT digest,data FROM workload_jobs WHERE scope=? AND idem=?",
                (principal_scope, idempotency_key),
            ).fetchone()
            if old:
                if old[0] != digest:
                    raise JobError(
                        "idempotency_conflict", "Idempotency key belongs to a different request."
                    )
                return JobRecord.model_validate_json(old[1])
        now = self.clock()
        spec.deadline_at = spec.deadline_at or now + spec.resource_budget.wall_seconds
        if spec.deadline_at <= now or spec.deadline_at > now + 604800:
            raise JobError(
                "invalid_deadline", "Job deadline is expired or exceeds seven days.", 422
            )
        if not spec.stages:
            spec.stages = [
                StageSpec(
                    task=spec.task,
                    parameters=spec.parameters,
                    input_artifacts=spec.input_artifacts,
                    outputs=spec.outputs,
                    placement_plan=spec.placement_plan,
                    budget=spec.resource_budget,
                )
            ]
        policy = self.get_policy().model_copy(deep=True)
        storage_policy = (
            self.storage.get_policy().model_copy(deep=True) if self.storage is not None else None
        )
        seen: set[str] = set()
        for stage in spec.stages:
            if stage.task in RESERVED_TASKS or stage.task == "live_detection":
                raise JobError("task_not_durable", "This task does not use the durable queue.", 422)
            if stage.stage_id in seen or any(dep not in seen for dep in stage.depends_on):
                raise JobError(
                    "invalid_stages", "Stages must be unique and ordered without cycles.", 422
                )
            seen.add(stage.stage_id)
            if len({slot.slot for slot in stage.outputs}) != len(stage.outputs):
                raise JobError("invalid_outputs", "Output slot names must be unique.", 422)
            plan = stage.placement_plan or self.placement.plan(
                stage.task,
                budget=stage.budget,
                policy_snapshot=policy,
            )
            if plan.task != stage.task:
                raise JobError("invalid_placement", "Placement does not match the stage task.", 422)
            stage.placement_plan = plan.model_copy(deep=True)
            stage.budget = plan.budget.model_copy(deep=True)
            if plan.selected_target.node_id == self.node_id:
                self.role_check("training" if stage.task == "training" else "analysis")
                self._validate_budget(stage.budget)
                if self.storage is not None and stage.workspace_admission is None:
                    stage.workspace_admission = self.storage.task_workspace_admission(
                        stage.task, max_bytes=stage.budget.workspace_bytes
                    )
            for slot in stage.outputs:
                slot.artifact_id = slot.artifact_id or str(
                    uuid5(UUID(spec.job_id), stage.stage_id + ":" + slot.slot)
                )
                if slot.admission is None and self.storage is not None:
                    slot.admission = self.storage.admit(
                        slot.kind,
                        origin_node_id=spec.origin_node_id,
                        policy_snapshot=storage_policy,
                    )
            if plan.selected_target.provider_id:
                if coordinator_node_id and coordinator_node_id != self.node_id:
                    try:
                        provider = ProviderDefinition.model_validate(
                            stage.parameters.get("_provider")
                        )
                    except ValueError:
                        raise JobError(
                            "invalid_provider", "Delegated provider snapshot is invalid.", 422
                        ) from None
                else:
                    provider = self.placement.provider(plan.selected_target.provider_id)
                if (
                    provider.provider_id != plan.selected_target.provider_id
                    or not provider.enabled
                    or stage.task not in provider.tasks
                ):
                    raise JobError(
                        "provider_unavailable", "Provider does not support this admitted task.", 422
                    )
                stage.parameters["_provider"] = provider.model_dump(mode="json")
        first = spec.stages[0].placement_plan
        record = JobRecord(
            job_id=spec.job_id,
            coordinator_node_id=coordinator_node_id or self.node_id,
            origin_node_id=spec.origin_node_id,
            task=spec.task,
            created_at=now,
            updated_at=now,
            deadline_at=spec.deadline_at,
            priority=spec.priority,
            requested_target=first.requested_target if first else None,
            actual_target=first.selected_target if first else None,
            stages=[
                StageRecord(stage_id=s.stage_id, task=s.task, placement_plan=s.placement_plan)
                for s in spec.stages
            ],
            reference=spec.reference,
        )
        try:
            self._pin_references(
                spec.job_id,
                [ref for stage in spec.stages for ref in stage.input_artifacts],
                spec.deadline_at,
            )
            saved = self._save_submission(spec, record, principal_scope, idempotency_key, digest)
        except BaseException:
            if self.get(spec.job_id) is None:
                self._release_pins(spec.job_id)
            raise
        if saved.job_id != spec.job_id:
            self._release_pins(spec.job_id)
        return saved

    def _save_submission(self, spec, record, principal_scope, idempotency_key, digest):
        now = record.created_at
        with self.journal.transaction() as conn:
            # A second caller may have completed admission while routes were resolved.
            old = conn.execute(
                "SELECT digest,data FROM workload_jobs WHERE scope=? AND idem=?",
                (principal_scope, idempotency_key),
            ).fetchone()
            if old:
                if old[0] != digest:
                    raise JobError(
                        "idempotency_conflict", "Idempotency key belongs to a different request."
                    )
                return JobRecord.model_validate_json(old[1])
            if not self._supervision_allowed(spec, conn):
                raise JobError(
                    "supervision_stopped", "Supervision no longer permits this experiment."
                )
            if conn.execute("SELECT 1 FROM workload_jobs WHERE id=?", (spec.job_id,)).fetchone():
                raise JobError("job_id_conflict", "Job identity already exists.")
            count = conn.execute(
                "SELECT COUNT(*) FROM workload_jobs WHERE state NOT IN "
                "('succeeded','failed','cancelled','deadline_exceeded')"
            ).fetchone()[0]
            if count >= self.config.jobs.max_queued:
                raise JobError("queue_full", "The durable workload queue is full.", 429)
            conn.execute(
                "INSERT INTO workload_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    spec.job_id,
                    principal_scope,
                    idempotency_key,
                    digest,
                    "queued",
                    spec.priority,
                    now,
                    spec.model_dump_json(),
                    record.model_dump_json(),
                ),
            )
            for stage in spec.stages:
                conn.execute(
                    "INSERT INTO workload_stages(job,stage,state) VALUES(?,?,'queued')",
                    (spec.job_id, stage.stage_id),
                )
            self.journal.event(spec.job_id, "submitted", conn)
        self._wake.set()
        return record

    def _pin_transport(self):
        if self.transport is not None:
            return self.transport
        from tailcam.jobs.transport import JobTransport

        return JobTransport(self.storage.resolve_peer)

    def _pin_references(self, job_id: str, references, deadline_at: float) -> None:
        if self.storage is None or deadline_at <= self.clock():
            return
        unique: dict[str, ArtifactRef] = {}
        for ref in references:
            ref = ArtifactRef.model_validate(ref)
            previous = unique.get(ref.artifact_id)
            if previous and (previous.owner_node_id, previous.sha256, previous.size_bytes) != (
                ref.owner_node_id,
                ref.sha256,
                ref.size_bytes,
            ):
                raise JobError(
                    "input_changed", "An artifact has conflicting input references.", 422
                )
            unique[ref.artifact_id] = ref
        for ref in unique.values():
            pin = ArtifactPin(
                pin_id=str(uuid5(UUID(job_id), self.node_id + ":artifact:" + ref.artifact_id)),
                coordinator_node_id=self.node_id,
                expires_at=deadline_at,
                sha256=ref.sha256,
                size_bytes=ref.size_bytes,
            )
            if ref.owner_node_id == self.node_id:
                self.storage.pin_artifact(ref.artifact_id, pin)
            else:
                self._pin_transport().pin_artifact(ref, pin)
            with self.journal.transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO workload_pins VALUES(?,?,?,?)",
                    (job_id, ref.artifact_id, ref.model_dump_json(), pin.model_dump_json()),
                )

    def _release_pins(self, job_id: str, *, limit: int = 10000, terminal_only=False) -> int:
        if self.storage is None:
            return 0
        lock_id = str(uuid5(UUID(self.node_id), "job-admission:" + job_id))
        try:
            with self.storage.pins.guard(lock_id):
                return self._release_pins_locked(job_id, limit=limit, terminal_only=terminal_only)
        except StorageError:
            return 0

    def _release_pins_locked(self, job_id, *, limit, terminal_only):
        record = self.get(job_id)
        released = 0
        for row in self.journal.connection.execute(
            "SELECT artifact,reference,pin FROM workload_pins WHERE job=? LIMIT ?", (job_id, limit)
        ).fetchall():
            ref, pin = (
                ArtifactRef.model_validate_json(row[1]),
                ArtifactPin.model_validate_json(row[2]),
            )
            if (
                terminal_only
                and record
                and record.state not in TERMINAL
                and pin.expires_at > self.clock()
            ):
                continue
            try:
                if pin.expires_at > self.clock():
                    if ref.owner_node_id == self.node_id:
                        self.storage.release_artifact_pin(
                            ref.artifact_id, pin.pin_id, coordinator_node_id=self.node_id
                        )
                    else:
                        self._pin_transport().release_artifact_pin(ref, pin)
                with self.journal.transaction() as conn:
                    conn.execute(
                        "DELETE FROM workload_pins WHERE job=? AND artifact=?", (job_id, row[0])
                    )
                released += 1
            except Exception:
                # A lost release acknowledgement retains the durable retry record. The
                # original immutable deadline still bounds the remote hold after shutdown.
                continue
        return released

    def release_terminal_pins(self, *, limit: int = 1) -> int:
        rows = self.journal.connection.execute(
            "SELECT DISTINCT p.job FROM workload_pins p LEFT JOIN workload_jobs j ON j.id=p.job "
            "WHERE j.state IN ('succeeded','failed','cancelled','deadline_exceeded') "
            "OR json_extract(p.pin,'$.expires_at')<=? LIMIT ?",
            (self.clock(), limit),
        ).fetchall()
        return sum(self._release_pins(row[0], limit=limit, terminal_only=True) for row in rows)

    def _validate_budget(self, budget):
        limits = self.config.jobs
        if budget.cpu_seconds < budget.wall_seconds * budget.cpu_threads:
            raise JobError(
                "unsupported_cpu_budget",
                "CPU allowance must cover wall time multiplied by admitted threads.",
                422,
            )
        if budget.require_hard_workspace_limit:
            raise JobError(
                "hard_workspace_limit_unavailable",
                "This worker cannot enforce a kernel scratch quota.",
                422,
            )
        if budget.require_hard_memory_limit and not getattr(
            self.executor, "supports_hard_memory_limit", False
        ):
            raise JobError(
                "hard_memory_limit_unavailable",
                "This worker cannot enforce the requested hard memory limit.",
                422,
            )
        if (
            budget.cpu_threads > limits.max_cpu_threads
            or budget.memory_bytes > limits.max_memory_bytes
            or budget.workspace_bytes > limits.max_workspace_bytes
            or budget.gpu_slots > limits.max_gpu_slots
        ):
            raise JobError(
                "budget_exceeded", "Task exceeds this worker's configured resource limits.", 507
            )

    def _fits(self, budget, task, conn):
        leases = [
            Lease.model_validate_json(row[0])
            for row in conn.execute(
                "SELECT lease FROM workload_stages WHERE lease IS NOT NULL AND state IN "
                "('leased','running','committing','cancel_requested')"
            )
        ]
        local = [lease for lease in leases if lease.worker_node_id == self.node_id]
        live = self.live_usage()
        budgets = [lease.spec.budget for lease in local] + live
        limits = self.config.jobs
        return (
            len(local) + len(live) < limits.max_running
            and (
                task != "training"
                or sum(lease.spec.task == "training" for lease in local) < limits.max_training
            )
            and sum(item.cpu_threads for item in budgets) + budget.cpu_threads
            <= limits.max_cpu_threads
            and sum(item.memory_bytes for item in budgets) + budget.memory_bytes
            <= limits.max_memory_bytes
            and sum(item.workspace_bytes for item in budgets) + budget.workspace_bytes
            <= limits.max_workspace_bytes
            and sum(item.gpu_slots for item in budgets) + budget.gpu_slots <= limits.max_gpu_slots
        )

    def claim_local(self, worker_session: str | None = None) -> Lease | None:
        return self._claim(worker_session or self.worker_session, remote=False)

    def _claim(self, worker_session: str, *, remote: bool) -> Lease | None:
        now = self.clock()
        with self.journal.transaction() as conn:
            rows = conn.execute(
                "SELECT spec,data FROM workload_jobs WHERE state IN "
                "('queued','retry_wait','waiting_for_worker') ORDER BY priority,created LIMIT 128"
            ).fetchall()
            for row in rows:
                spec, record = (
                    JobSpec.model_validate_json(row[0]),
                    JobRecord.model_validate_json(row[1]),
                )
                if not self._supervision_allowed(spec, conn):
                    self._terminal(record, "cancelled", conn)
                    continue
                if record.deadline_at <= now:
                    self._terminal(record, "deadline_exceeded", conn)
                    continue
                for stage_spec, stage in zip(spec.stages, record.stages, strict=True):
                    if stage.state not in {"queued", "retry_wait", "waiting_for_worker"}:
                        continue
                    if any(
                        next(s for s in record.stages if s.stage_id == dependency).state
                        != "succeeded"
                        for dependency in stage_spec.depends_on
                    ):
                        continue
                    plan = stage_spec.placement_plan
                    assert plan is not None
                    worker = plan.selected_target.node_id or self.node_id
                    if (worker != self.node_id) != remote:
                        continue
                    data = conn.execute(
                        "SELECT retry_at FROM workload_stages WHERE job=? AND stage=?",
                        (record.job_id, stage.stage_id),
                    ).fetchone()
                    if data[0] > now:
                        continue
                    if not remote:
                        try:
                            self.role_check("training" if stage.task == "training" else "analysis")
                            self._validate_budget(stage_spec.budget)
                        except (RoleDisabledError, JobError):
                            stage.error = SafeError(
                                code="role_or_budget_unavailable",
                                detail="Worker roles or budgets no longer permit execution",
                            )
                            self._terminal(record, "failed", conn, stage.error)
                            break
                        if not self._fits(stage_spec.budget, stage.task, conn):
                            continue
                    inputs = stage_spec.model_copy(deep=True)
                    for dependency in inputs.depends_on:
                        inputs.input_artifacts.extend(
                            next(s for s in record.stages if s.stage_id == dependency).outputs
                        )
                    attempt_deadline = min(record.deadline_at, now + stage_spec.budget.wall_seconds)
                    lease = Lease(
                        job_id=record.job_id,
                        stage_id=stage.stage_id,
                        attempt_id=str(uuid4()),
                        fence_token=secrets.token_urlsafe(32),
                        worker_session=worker_session,
                        worker_node_id=worker,
                        expires_at=min(now + self.config.jobs.lease_seconds, attempt_deadline),
                        deadline_at=attempt_deadline,
                        attempt=stage.attempt + 1,
                        spec=inputs,
                    )
                    stage.state, stage.attempt, stage.worker_node_id = (
                        "leased",
                        lease.attempt,
                        worker,
                    )
                    stage.heartbeat_at = now
                    record.state, record.updated_at = "leased", now
                    conn.execute(
                        "UPDATE workload_stages SET state='leased',lease=? WHERE job=? AND stage=?",
                        (lease.model_dump_json(), record.job_id, stage.stage_id),
                    )
                    self.journal.save(record, conn, "leased")
                    return lease
        return None

    def _fenced(self, lease: Lease, conn, *, committing=False, allow_cancel=False):
        row = conn.execute(
            "SELECT lease,permit FROM workload_stages WHERE job=? AND stage=?",
            (lease.job_id, lease.stage_id),
        ).fetchone()
        if row is None or row[0] is None:
            raise JobError("stale_lease", "Worker lease is no longer current.")
        current = Lease.model_validate_json(row[0])
        if (
            current.attempt_id != lease.attempt_id
            or current.fence_token != lease.fence_token
            or current.worker_session != lease.worker_session
        ):
            raise JobError("stale_lease", "Worker lease is no longer current.")
        record = self.journal.get(lease.job_id)
        if record is None:
            raise JobError("job_missing", "Job does not exist.", 404)
        stage = next(s for s in record.stages if s.stage_id == lease.stage_id)
        if not committing and (
            current.expires_at <= self.clock()
            or stage.state not in {"leased", "running"}
            or (record.cancel_requested and not allow_cancel)
            or record.state in TERMINAL
        ):
            raise JobError("stale_lease", "Worker lease is expired or cancelled.")
        return record, stage, current, row[1]

    def start_attempt(self, lease: Lease) -> JobRecord:
        with self.journal.transaction() as conn:
            record, stage, _, _ = self._fenced(lease, conn)
            now = self.clock()
            stage.state, stage.started_at = "running", stage.started_at or now
            record.state, record.started_at, record.updated_at = (
                "running",
                record.started_at or now,
                now,
            )
            conn.execute(
                "UPDATE workload_stages SET state='running' WHERE job=? AND stage=?",
                (lease.job_id, lease.stage_id),
            )
            self.journal.save(record, conn, "started")
            return record

    def heartbeat(self, lease: Lease, progress: Progress | None = None) -> Lease:
        with self.journal.transaction() as conn:
            record, stage, current, _ = self._fenced(lease, conn, allow_cancel=True)
            now = self.clock()
            current.expires_at = min(now + self.config.jobs.lease_seconds, current.deadline_at)
            stage.heartbeat_at = now
            if progress is not None:
                stage.progress = Progress.model_validate(progress)
            record.updated_at = now
            conn.execute(
                "UPDATE workload_stages SET lease=? WHERE job=? AND stage=?",
                (current.model_dump_json(), lease.job_id, lease.stage_id),
            )
            self.journal.save(record, conn)
            return current

    def is_cancel_requested(self, lease_or_job_id) -> bool:
        job_id = lease_or_job_id.job_id if isinstance(lease_or_job_id, Lease) else lease_or_job_id
        record = self.get(job_id)
        if (
            record is None
            or record.cancel_requested
            or record.state in TERMINAL
            or self._stop.is_set()
        ):
            return True
        if record.deadline_at <= self.clock():
            return True
        if isinstance(lease_or_job_id, Lease):
            return lease_or_job_id.deadline_at <= self.clock()
        return False

    def prepare_result(
        self, lease: Lease, manifest: ResultManifest | dict, *, _remote_terminal=False
    ) -> PublicationPermit:
        manifest = ResultManifest.model_validate(manifest)
        with self.journal.transaction() as conn:
            # Replay the selected manifest even after a lost acknowledgement or lease expiry.
            record, stage, current, old = self._fenced(lease, conn, committing=True)
            if old:
                permit = PublicationPermit.model_validate_json(old)
                if permit.manifest != manifest:
                    raise JobError("result_conflict", "A different result was already selected.")
                return permit
            self._fenced(lease, conn, allow_cancel=_remote_terminal)
            slots = {slot.slot: slot for slot in current.spec.outputs}
            if (
                set(slots) != {output.slot for output in manifest.outputs}
                or len(manifest.outputs) != len(slots)
                or sum(output.size_bytes for output in manifest.outputs)
                > current.spec.budget.output_bytes
            ):
                raise JobError(
                    "invalid_result",
                    "Result slots or byte count do not match the admitted task.",
                    422,
                )
            permit = PublicationPermit(
                job_id=lease.job_id,
                origin_node_id=record.origin_node_id,
                stage_id=lease.stage_id,
                attempt_id=lease.attempt_id,
                fence_token=lease.fence_token,
                permit_id=str(uuid4()),
                manifest=manifest,
                slots=current.spec.outputs,
            )
            stage.state, record.state = "committing", "committing"
            record.updated_at = self.clock()
            conn.execute(
                "UPDATE workload_stages SET state='committing',permit=? WHERE job=? AND stage=?",
                (permit.model_dump_json(), lease.job_id, lease.stage_id),
            )
            self.journal.save(record, conn, "publication_selected")
            return permit

    def commit_result(
        self, permit: PublicationPermit, artifacts: builtins.list[ArtifactRef]
    ) -> JobRecord:
        permit = PublicationPermit.model_validate(permit)
        refs = [ArtifactRef.model_validate(item) for item in artifacts]
        selected_row = self.journal.connection.execute(
            "SELECT permit FROM workload_stages WHERE job=? AND stage=?",
            (permit.job_id, permit.stage_id),
        ).fetchone()
        if (
            not selected_row
            or not selected_row[0]
            or PublicationPermit.model_validate_json(selected_row[0]) != permit
        ):
            raise JobError("stale_permit", "Publication permit is no longer current.")
        expected_slots = {slot.slot: slot for slot in permit.slots}
        expected_outputs = {output.slot: output for output in permit.manifest.outputs}
        if len(refs) != len(expected_slots) or {ref.slot for ref in refs} != set(expected_slots):
            raise JobError("invalid_commit", "Committed output slots do not match.", 422)
        for ref in refs:
            output = expected_outputs[ref.slot]
            if (ref.artifact_id, ref.sha256, ref.size_bytes) != (
                expected_slots[ref.slot].artifact_id,
                output.sha256,
                output.size_bytes,
            ):
                raise JobError(
                    "invalid_commit", "Committed output differs from the selected result.", 422
                )
        # Hold intermediate outputs before another stage can be admitted. This occurs
        # outside the result transaction and reuses immutable idempotent owner pin IDs.
        selected = self.get(permit.job_id)
        if selected is not None and selected.state not in TERMINAL:
            self._pin_references(permit.job_id, refs, selected.deadline_at)
        with self.journal.transaction() as conn:
            row = conn.execute(
                "SELECT permit FROM workload_stages WHERE job=? AND stage=?",
                (permit.job_id, permit.stage_id),
            ).fetchone()
            if (
                row is None
                or row[0] is None
                or PublicationPermit.model_validate_json(row[0]) != permit
            ):
                raise JobError("stale_permit", "Publication permit is no longer current.")
            slots = {slot.slot: slot for slot in permit.slots}
            outputs = {output.slot: output for output in permit.manifest.outputs}
            if len(refs) != len(slots) or {ref.slot for ref in refs} != set(slots):
                raise JobError("invalid_commit", "Committed output slots do not match.", 422)
            for ref in refs:
                expected = outputs[ref.slot]
                if (
                    ref.artifact_id != slots[ref.slot].artifact_id
                    or ref.sha256 != expected.sha256
                    or ref.size_bytes != expected.size_bytes
                ):
                    raise JobError(
                        "invalid_commit",
                        "Committed artifact does not match the selected result.",
                        422,
                    )
                if self.storage is not None:
                    saved = self.storage.catalog.get(ref.artifact_id)
                    if (
                        saved is None
                        or saved.state not in {"committed", "replicated"}
                        or ArtifactRef.from_artifact(saved, ref.slot) != ref
                    ):
                        raise JobError("unverified_commit", "Storage has not verified this output.")
            record = self.journal.get(permit.job_id)
            assert record is not None
            stage = next(s for s in record.stages if s.stage_id == permit.stage_id)
            if stage.state == "succeeded":
                return record
            stage.state, stage.outputs, stage.result = "succeeded", refs, permit.manifest.result
            stage.ended_at = self.clock()
            stage.progress.fraction = 1.0
            conn.execute(
                "UPDATE workload_stages SET state='succeeded',lease=NULL WHERE job=? AND stage=?",
                (permit.job_id, permit.stage_id),
            )
            if all(s.state == "succeeded" for s in record.stages):
                self._terminal(record, "succeeded", conn)
            elif record.cancel_requested:
                self._terminal(record, "cancelled", conn)
            else:
                record.state, record.updated_at = "queued", self.clock()
                self.journal.save(record, conn, "stage_committed")
        self._wake.set()
        if record.state in TERMINAL:
            self._release_pins(record.job_id)
        return record

    def commit_remote(
        self, lease: Lease, artifacts: builtins.list[ArtifactRef], result: dict
    ) -> JobRecord:
        """Reconcile the selected worker's committed slots against their actual storage owner."""
        from tailcam.storage.models import Artifact

        refs = [ArtifactRef.model_validate(item) for item in artifacts]
        for ref in refs:
            if self.storage is None:
                continue
            artifact = self.storage.catalog.get(ref.artifact_id)
            if artifact is None:
                artifact = Artifact.model_validate(
                    self.storage._json(
                        ref.owner_node_id, "GET", f"/api/v1/artifacts/{ref.artifact_id}"
                    )
                )
                if ArtifactRef.from_artifact(artifact, ref.slot) != ref:
                    raise JobError("invalid_commit", "Remote output metadata does not match.")
                self.storage.catalog.import_index(
                    ref.owner_node_id, [artifact.model_dump(mode="json")]
                )
        from tailcam.jobs.models import PreparedOutput

        manifest = ResultManifest(
            outputs=[
                PreparedOutput(
                    slot=ref.slot,
                    path="remote/" + ref.artifact_id,
                    size_bytes=ref.size_bytes,
                    sha256=ref.sha256,
                )
                for ref in refs
            ],
            result=result,
        )
        return self.commit_result(self.prepare_result(lease, manifest, _remote_terminal=True), refs)

    def pending_publications(self) -> builtins.list[PublicationPermit]:
        return [
            PublicationPermit.model_validate_json(row[0])
            for row in self.journal.connection.execute(
                "SELECT permit FROM workload_stages WHERE state='committing' "
                "AND permit IS NOT NULL LIMIT 128"
            )
        ]

    def set_workspace(self, lease: Lease, path: str) -> None:
        with self.journal.transaction() as conn:
            self._fenced(lease, conn)
            key = "workspace:" + lease.attempt_id
            previous = self.journal.setting(key)
            if previous is not None and previous != str(path):
                raise JobError("workspace_conflict", "Attempt workspace identity is frozen.")
            self.journal.set_setting(key, str(path), conn)

    def get_workspace(self, attempt_id: str) -> str | None:
        return self.journal.setting("workspace:" + attempt_id)

    def fail(self, lease: Lease, error: SafeError | dict) -> JobRecord:
        error = SafeError.model_validate(error)
        with self.journal.transaction() as conn:
            record, stage, _, permit = self._fenced(lease, conn, committing=True)
            if permit:
                # A chosen result is recoverable delivery, never a new computation attempt.
                stage.error = error
                record.updated_at = self.clock()
                self.journal.save(record, conn, "publication_waiting")
                return record
            self._fail_stage(record, stage, error, conn)
            return record

    def _fail_stage(self, record, stage, error, conn):
        now, spec = self.clock(), self.spec(record.job_id)
        stage.error = error
        if record.cancel_requested:
            self._terminal(record, "cancelled", conn)
        elif now >= record.deadline_at or error.code == "deadline_exceeded":
            self._terminal(record, "deadline_exceeded", conn)
        elif error.retryable and error.code in _RETRYABLE and stage.attempt < spec.max_attempts:
            stage.state, record.state = "retry_wait", "retry_wait"
            conn.execute(
                "UPDATE workload_stages SET state='retry_wait',lease=NULL,retry_at=? "
                "WHERE job=? AND stage=?",
                (now + min(30, 2**stage.attempt), record.job_id, stage.stage_id),
            )
            record.updated_at = now
            self.journal.save(record, conn, "retry_scheduled")
        else:
            self._terminal(record, "failed", conn, error)

    def _terminal(self, record, state, conn, error=None):
        record.state, record.ended_at, record.updated_at = state, self.clock(), self.clock()
        record.error = error
        record.allowed_actions = []
        if (
            state == "failed"
            and record.deadline_at > self.clock()
            and any(
                stage.state not in TERMINAL
                and stage.attempt < self.spec(record.job_id).max_attempts
                for stage in record.stages
            )
        ):
            record.allowed_actions = ["retry"]
        for stage in record.stages:
            if stage.state not in TERMINAL and stage.state != "committing":
                stage.state, stage.ended_at = state, self.clock()
                conn.execute(
                    "UPDATE workload_stages SET state=?,lease=NULL WHERE job=? AND stage=?",
                    (state, record.job_id, stage.stage_id),
                )
        self.journal.save(record, conn, "terminal")

    def request_cancel(self, job_id: str) -> JobRecord:
        with self.journal.transaction() as conn:
            record = self.journal.get(job_id)
            if record is None:
                raise JobError("job_missing", "Job does not exist.", 404)
            if record.state in TERMINAL:
                return record
            record.cancel_requested = True
            record.updated_at = self.clock()
            record.allowed_actions = []
            if record.state in {"queued", "retry_wait", "waiting_for_worker"}:
                self._terminal(record, "cancelled", conn)
            elif record.state != "committing":
                record.state = "cancel_requested"
                self.journal.save(record, conn, "cancel_requested")
            else:
                self.journal.save(record, conn, "cancel_after_publication")
            return record

    def retry(self, job_id: str) -> JobRecord:
        if self.storage is None:
            return self._retry(job_id)
        lock_id = str(uuid5(UUID(self.node_id), "job-admission:" + job_id))
        with self.storage.pins.guard(lock_id):
            record = self.get(job_id)
            if record is None:
                raise JobError("job_missing", "Job does not exist.", 404)
            spec = self.spec(job_id)
            if record.state != "failed" or record.deadline_at <= self.clock():
                raise JobError("retry_not_allowed", "This job cannot be retried.")
            if any(
                stage.attempt >= spec.max_attempts
                for stage in record.stages
                if stage.state != "succeeded"
            ):
                raise JobError("attempt_budget_exhausted", "The job attempt budget is exhausted.")
            try:
                self._pin_references(
                    job_id,
                    [ref for stage in spec.stages for ref in stage.input_artifacts]
                    + [ref for stage in record.stages for ref in stage.outputs],
                    record.deadline_at,
                )
                return self._retry(job_id)
            except BaseException:
                self._release_pins(job_id, terminal_only=True)
                raise

    def _retry(self, job_id: str) -> JobRecord:
        with self.journal.transaction() as conn:
            record = self.journal.get(job_id)
            if record is None:
                raise JobError("job_missing", "Job does not exist.", 404)
            spec = self.spec(job_id)
            if record.state != "failed" or record.deadline_at <= self.clock():
                raise JobError("retry_not_allowed", "This job cannot be retried.")
            candidates = [stage for stage in record.stages if stage.state != "succeeded"]
            if any(stage.attempt >= spec.max_attempts for stage in candidates):
                raise JobError("attempt_budget_exhausted", "The job attempt budget is exhausted.")
            for stage in candidates:
                stage.state, stage.error, stage.ended_at = "queued", None, None
                conn.execute(
                    "UPDATE workload_stages SET state='queued',lease=NULL,retry_at=0 "
                    "WHERE job=? AND stage=?",
                    (job_id, stage.stage_id),
                )
            record.state, record.error, record.ended_at = "queued", None, None
            record.allowed_actions, record.updated_at = ["cancel"], self.clock()
            self.journal.save(record, conn, "manual_retry")
        self._wake.set()
        return record

    def recover(self) -> int:
        changed = 0
        now = self.clock()
        with self.journal.transaction() as conn:
            for row in conn.execute(
                "SELECT data FROM workload_jobs WHERE state NOT IN "
                "('succeeded','failed','cancelled','deadline_exceeded')"
            ).fetchall():
                record = JobRecord.model_validate_json(row[0])
                if record.state == "committing":
                    continue
                if record.deadline_at <= now and record.state not in _ACTIVE:
                    self._terminal(record, "deadline_exceeded", conn)
                    changed += 1
                    continue
                for stage in record.stages:
                    saved = conn.execute(
                        "SELECT lease FROM workload_stages WHERE job=? AND stage=?",
                        (record.job_id, stage.stage_id),
                    ).fetchone()
                    if saved and saved[0]:
                        lease = Lease.model_validate_json(saved[0])
                        if lease.expires_at <= now:
                            deadline_elapsed = lease.deadline_at <= now
                            self._fail_stage(
                                record,
                                stage,
                                SafeError(
                                    code="deadline_exceeded" if deadline_elapsed else "worker_lost",
                                    detail="Attempt deadline elapsed"
                                    if deadline_elapsed
                                    else "Worker lease expired; attempt is no longer authorized",
                                    retryable=not deadline_elapsed,
                                ),
                                conn,
                            )
                            changed += 1
                            break
        return changed

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="workload-queue", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        with self._lock:
            running = list(self._running.values())
        for thread in running:
            thread.join(timeout=1)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.recover()
                self.release_terminal_pins()
                with self._lock:
                    available = len(self._running) < self.config.jobs.max_running
                if available:
                    for permit in self.pending_publications():
                        with self._lock:
                            if (
                                permit.attempt_id in self._running
                                or self._publication_retry_after.get(permit.attempt_id, 0)
                                > time.monotonic()
                                or len(self._running) >= self.config.jobs.max_running
                            ):
                                continue
                            thread = threading.Thread(
                                target=self._recover_publication,
                                args=(permit,),
                                name="publication-recovery",
                                daemon=True,
                            )
                            self._running[permit.attempt_id] = thread
                        thread.start()
                    with self._lock:
                        available = len(self._running) < self.config.jobs.max_running
                if available:
                    lease = self.claim_local() if self.executor is not None else None
                    if lease is None and self.transport is not None:
                        lease = self._claim(self.worker_session, remote=True)
                    if lease is not None:
                        thread = threading.Thread(
                            target=self._execute,
                            args=(lease,),
                            name="workload-" + lease.attempt_id[:8],
                            daemon=True,
                        )
                        with self._lock:
                            self._running[lease.attempt_id] = thread
                        thread.start()
            except Exception:
                # Public state carries safe errors; loop never emits raw endpoint/runtime secrets.
                pass
            self._wake.wait(self.config.jobs.poll_seconds)
            self._wake.clear()

    def _recover_publication(self, permit):
        try:
            refs = []
            if self.storage is not None:
                for slot, output in zip(permit.slots, permit.manifest.outputs, strict=True):
                    # Manifest output order need not be slot order.
                    output = next(
                        item for item in permit.manifest.outputs if item.slot == slot.slot
                    )
                    saved = self.storage.catalog.get(slot.artifact_id)
                    if (
                        saved is None
                        or saved.state not in {"committed", "replicated"}
                        or saved.sha256 != output.sha256
                        or saved.size_bytes != output.size_bytes
                    ):
                        break
                    refs.append(ArtifactRef.from_artifact(saved, slot.slot))
            if len(refs) == len(permit.slots):
                self.commit_result(permit, refs)
                workspace = self.get_workspace(permit.attempt_id)
                if workspace and self.storage is not None:
                    self.storage.release_workspace(Path(workspace))
            elif self.executor is not None and hasattr(self.executor, "recover"):
                self.executor.recover(permit, self)
        except Exception:
            # Keep the selected manifest and reservations; retry delivery, never computation.
            pass
        finally:
            with self._lock:
                self._running.pop(permit.attempt_id, None)
                self._publication_retry_after[permit.attempt_id] = time.monotonic() + 5

    def _execute(self, lease):
        done = threading.Event()

        def beat():
            while not done.wait(min(2, self.config.jobs.lease_seconds / 3)):
                try:
                    self.heartbeat(lease)
                except JobError:
                    return

        heartbeat = threading.Thread(target=beat, name="workload-heartbeat", daemon=True)
        heartbeat.start()
        try:
            executor = self.executor if lease.worker_node_id == self.node_id else self.transport
            executor.execute(lease, self)
        except Exception:
            try:
                self.fail(
                    lease, SafeError(code="executor_failed", detail="Worker execution failed")
                )
            except JobError:
                pass
        finally:
            done.set()
            heartbeat.join(timeout=1)
            with self._lock:
                self._running.pop(lease.attempt_id, None)
            self._wake.set()
