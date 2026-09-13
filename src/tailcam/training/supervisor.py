"""Durable, finite experiment approval and reconciliation over the existing trainer.

The journal reserves a whole experiment deadline before preparing its data. A
lost response retains that reservation and job identifier; reconnects never
create a second experiment. There is no embedded agent or activation operation.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4, uuid5

from tailcam.jobs.models import TERMINAL, JobError
from tailcam.training.supervisor_models import (
    AgentHeartbeat,
    ExperimentRecord,
    ExperimentRequest,
    SupervisionCreate,
    SupervisionRecord,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS training_supervisions (
 id TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS training_experiments (
 supervision TEXT NOT NULL, idem TEXT NOT NULL, digest TEXT NOT NULL,
 experiment TEXT NOT NULL UNIQUE, job TEXT NOT NULL UNIQUE,
 PRIMARY KEY(supervision,idem)
);
CREATE TABLE IF NOT EXISTS training_supervision_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, supervision TEXT NOT NULL, data TEXT NOT NULL
);
"""


class TrainingSupervisor:
    def __init__(self, store, jobs, training, node_id: str, *, clock=time.time):
        self.store, self.jobs, self.training = store, jobs, training
        self.node_id, self.clock = node_id, clock
        self.store._conn().executescript(_SCHEMA)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @contextmanager
    def _transaction(self):
        conn = self.store._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _read(self, ident: str, conn=None) -> SupervisionRecord:
        connection = conn if conn is not None else self.store._conn()
        row = connection.execute(
            "SELECT data FROM training_supervisions WHERE id=?", (ident,)
        ).fetchone()
        if row is None:
            raise JobError("supervision_not_found", "Supervision was not found", 404)
        return SupervisionRecord.model_validate_json(row[0])

    def _save(self, record: SupervisionRecord, conn, event: str):
        record.revision += 1
        record.updated_at = self.clock()
        conn.execute(
            "UPDATE training_supervisions SET state=?,data=? WHERE id=?",
            (record.state, record.model_dump_json(), record.supervision_id),
        )
        conn.execute(
            "INSERT INTO training_supervision_events(supervision,data) VALUES(?,?)",
            (
                record.supervision_id,
                json.dumps(
                    {
                        "kind": event,
                        "state": record.state,
                        "revision": record.revision,
                        "created_at": record.updated_at,
                    }
                ),
            ),
        )

    def create(self, request: SupervisionCreate, *, owner: str) -> SupervisionRecord:
        request = SupervisionCreate.model_validate(request.model_dump())
        dataset = self.store.get_dataset(request.objective.dataset_id)
        if dataset is None:
            raise JobError("dataset_not_found", "Approved dataset was not found", 404)
        if self.training.dataset_revision(dataset.id) != request.objective.dataset_revision:
            raise JobError("dataset_revision_changed", "Review the current dataset revision")
        if dataset.task != request.objective.task:
            raise JobError("dataset_task_mismatch", "Objective must match the dataset task")
        if any(self.store.get_model(i) is None for i in request.policy.allowed_model_ids):
            raise JobError("model_not_found", "Every approved model must be registered", 404)
        now = self.clock()
        record = SupervisionRecord(
            supervision_id=str(uuid4()),
            node_id=self.node_id,
            owner=owner,
            objective=request.objective,
            policy=request.policy,
            created_at=now,
            updated_at=now,
            remaining_experiments=request.policy.max_experiments,
            remaining_wall_seconds=request.policy.total_wall_seconds,
        )
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO training_supervisions VALUES(?,?,?,?)",
                (
                    record.supervision_id,
                    record.state,
                    now,
                    record.model_dump_json(),
                ),
            )
            self._save(record, conn, "approved")
        return self._decorate(record)

    def _decorate(self, record: SupervisionRecord) -> SupervisionRecord:
        record.agent_connected = bool(
            record.agent_heartbeat_at is not None
            and self.clock() - record.agent_heartbeat_at <= record.policy.agent_timeout_seconds
        )
        record.allowed_actions = ["inspect"]
        if record.state == "active":
            record.allowed_actions = [
                action
                for action in record.policy.permitted_actions
                if action != "experiment" or record.current_job_id is None
            ]
        elif record.state == "stop_requested":
            record.allowed_actions.append("stop")
        return record

    def _reconcile(self, record: SupervisionRecord) -> bool:
        """Only inspect durable jobs here; reading never starts a new experiment."""
        changed = False
        current = None
        failed = False
        for experiment in record.experiments:
            try:
                job = self.jobs.get(experiment.job_id)
            except JobError as exc:
                if exc.status_code != 404:
                    raise
                job = None
            if job is None:
                if experiment.state == "reserved":
                    current = experiment.job_id
                continue
            if experiment.job is None or job.revision != experiment.job.revision:
                experiment.job, experiment.state = job, job.state
                changed = True
            if job.state not in TERMINAL:
                current = job.job_id
            elif job.state in {"failed", "deadline_exceeded"}:
                failed = True
        if current != record.current_job_id:
            record.current_job_id = current
            changed = True
        if current is None and record.state in {"active", "stop_requested"}:
            if record.state == "stop_requested":
                record.state, record.reason = "stopped", "All experiment workers have stopped"
            elif failed:
                record.state, record.reason = "failed", "An experiment failed; inspect its evidence"
            elif record.remaining_experiments == 0:
                record.state, record.reason = "completed", "The approved experiments have finished"
            elif record.remaining_wall_seconds < record.policy.experiment_budget.wall_seconds:
                record.state = "budget_exhausted"
                record.reason = "The remaining budget cannot admit another experiment"
            else:
                return changed
            changed = True
        return changed

    def get(self, ident: str) -> SupervisionRecord:
        with self._transaction() as conn:
            record = self._read(ident, conn)
            if self._reconcile(record):
                self._save(record, conn, "job_progress")
        # Domain projections may own their own transactions. Never invoke them
        # while holding the supervisor transaction.
        for experiment in record.experiments:
            if experiment.job is not None:
                self.training.project_job(experiment.job)
        return self._decorate(record)

    def list(self, *, limit=50, cursor=0) -> dict:
        limit, cursor = min(100, max(1, limit)), max(0, cursor)
        rows = (
            self.store._conn()
            .execute(
                "SELECT id FROM training_supervisions ORDER BY created DESC,id LIMIT ? OFFSET ?",
                (limit + 1, cursor),
            )
            .fetchall()
        )
        return {
            "items": [self.get(row[0]) for row in rows[:limit]],
            "next_cursor": str(cursor + limit) if len(rows) > limit else None,
        }

    def heartbeat(self, ident: str, heartbeat: AgentHeartbeat) -> SupervisionRecord:
        with self._transaction() as conn:
            record = self._read(ident, conn)
            now = self.clock()
            if heartbeat.next_check_at is not None and heartbeat.next_check_at < now:
                raise JobError("invalid_check_time", "Next check must be in the future", 422)
            record.agent_session_id = heartbeat.session_id
            record.agent_heartbeat_at = now
            record.last_decision, record.reason = heartbeat.decision, heartbeat.reason
            record.next_check_at = heartbeat.next_check_at
            self._save(record, conn, "agent_heartbeat")
        return self._decorate(record)

    @staticmethod
    def _validate_experiment(record: SupervisionRecord, request: ExperimentRequest):
        policy = record.policy
        if record.state != "active" or "experiment" not in policy.permitted_actions:
            raise JobError("supervision_inactive", "This approval does not permit a new experiment")
        if record.current_job_id:
            raise JobError("experiment_active", "Wait for the current experiment to stop")
        if (
            record.remaining_experiments < 1
            or record.remaining_wall_seconds < policy.experiment_budget.wall_seconds
        ):
            raise JobError("supervision_budget_exhausted", "The approved budget is exhausted")
        if (
            request.base_model_id not in policy.allowed_model_ids
            or request.worker_node_id not in policy.allowed_worker_node_ids
            or request.seed not in policy.allowed_seeds
            or not policy.epochs.minimum <= request.epochs <= policy.epochs.maximum
            or not policy.image_size.minimum <= request.image_size <= policy.image_size.maximum
        ):
            raise JobError(
                "experiment_outside_policy", "Experiment exceeds the approved policy", 403
            )

    def experiment(self, ident: str, request: ExperimentRequest) -> SupervisionRecord:
        request = ExperimentRequest.model_validate(request.model_dump())
        digest = hashlib.sha256(
            json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        replay = False
        with self._transaction() as conn:
            record = self._read(ident, conn)
            self._reconcile(record)
            old = conn.execute(
                "SELECT digest FROM training_experiments WHERE supervision=? AND idem=?",
                (ident, request.idempotency_key),
            ).fetchone()
            if old is not None:
                if old[0] != digest:
                    raise JobError(
                        "idempotency_conflict", "This key identifies a different experiment"
                    )
                replay = True
            else:
                self._validate_experiment(record, request)
                experiment_id = str(uuid5(UUID(ident), request.idempotency_key))
                job_id = str(uuid5(UUID(experiment_id), "training"))
                item = ExperimentRecord(
                    experiment_id=experiment_id,
                    job_id=job_id,
                    request=request,
                    created_at=self.clock(),
                    reserved_wall_seconds=record.policy.experiment_budget.wall_seconds,
                )
                conn.execute(
                    "INSERT INTO training_experiments VALUES(?,?,?,?,?)",
                    (
                        ident,
                        request.idempotency_key,
                        digest,
                        experiment_id,
                        job_id,
                    ),
                )
                record.experiments.append(item)
                record.remaining_experiments -= 1
                record.remaining_wall_seconds -= item.reserved_wall_seconds
                record.current_job_id = job_id
                record.last_decision, record.reason = "experiment", request.reason
                self._save(record, conn, "experiment_reserved")
        if replay:
            # A concurrent original request may still be preparing its immutable
            # input. A replay observes that reservation rather than racing it.
            return self.get(ident)
        self._enqueue(record, item)
        return self.get(ident)

    def _enqueue(self, record: SupervisionRecord, item: ExperimentRecord):
        request, objective, policy = item.request, record.objective, record.policy
        try:
            spec = self.training.prepare_training_job(
                dataset_id=objective.dataset_id,
                dataset_revision=objective.dataset_revision,
                base_model_id=request.base_model_id,
                epochs=request.epochs,
                image_size=request.image_size,
                seed=request.seed,
                worker_node_id=request.worker_node_id,
                camera_ids=objective.camera_ids,
                classes=objective.classes,
                job_id=item.job_id,
                supervision_id=record.supervision_id,
                experiment_id=item.experiment_id,
                budget=policy.experiment_budget.model_copy(deep=True),
                max_attempts=policy.max_attempts,
            )
            # Preparation is part of the accepted wall deadline, too.
            spec.deadline_at = item.created_at + item.reserved_wall_seconds
            if self._read(record.supervision_id).state != "active":
                with self._transaction() as conn:
                    current = self._read(record.supervision_id, conn)
                    for experiment in current.experiments:
                        if experiment.experiment_id == item.experiment_id:
                            experiment.state = "cancelled"
                    current.current_job_id = None
                    current.state, current.reason = (
                        "stopped",
                        "Input preparation stopped before execution",
                    )
                    self._save(current, conn, "stopped_before_enqueue")
                return
            self.jobs.submit(
                spec,
                idempotency_key=item.experiment_id,
                principal_scope=f"supervision:{record.supervision_id}",
            )
            if self._read(record.supervision_id).state == "stop_requested":
                self.jobs.request_cancel(item.job_id)
        except Exception:
            # Job submission might have committed before the caller lost its
            # response. Reconciliation, never unbounded resubmission, decides.
            with self._transaction() as conn:
                current = self._read(record.supervision_id, conn)
                if not self._reconcile(current):
                    stopped = current.state == "stop_requested"
                    for experiment in current.experiments:
                        if experiment.experiment_id == item.experiment_id:
                            experiment.error_code = (
                                None if stopped else "experiment_preparation_failed"
                            )
                            experiment.state = "cancelled" if stopped else "failed"
                    current.state, current.current_job_id = "stopped" if stopped else "failed", None
                    current.reason = (
                        "Stop prevented late experiment submission"
                        if stopped
                        else "Preparation failed; reservation and diagnostic reference remain"
                    )
                    self._save(
                        current, conn, "stopped_before_enqueue" if stopped else "preparation_failed"
                    )

    def stop(self, ident: str) -> SupervisionRecord:
        with self._transaction() as conn:
            record = self._read(ident, conn)
            self._reconcile(record)
            if record.state not in {"active", "stop_requested"}:
                return self._decorate(record)
            record.state, record.last_decision = "stop_requested", "stop"
            record.reason = "Stop requested; waiting for worker termination or committed results"
            # A reserved input preparation has no worker yet. It must observe
            # the state again before enqueueing (and recovery will not start it).
            self._save(record, conn, "stop_requested")
            job_id = record.current_job_id
        if job_id:
            try:
                self.jobs.request_cancel(job_id)
            except JobError as exc:
                if exc.status_code != 404:
                    raise
        return self.get(ident)

    def finish(self, ident: str, *, reason: str) -> SupervisionRecord:
        with self._transaction() as conn:
            record = self._read(ident, conn)
            self._reconcile(record)
            if record.current_job_id is not None:
                raise JobError("experiment_active", "Stop or finish the current experiment first")
            if record.state == "active":
                if "finish" not in record.policy.permitted_actions:
                    raise JobError(
                        "action_not_approved", "Finishing is not an approved action", 403
                    )
                record.state = "completed"
                record.last_decision, record.reason = "finish", reason[:1024]
                self._save(record, conn, "finished")
        return self._decorate(record)

    def events(self, ident: str, *, after=0, limit=100) -> dict:
        self._read(ident)
        rows = (
            self.store._conn()
            .execute(
                "SELECT sequence,data FROM training_supervision_events "
                "WHERE supervision=? AND sequence>? ORDER BY sequence LIMIT ?",
                (ident, max(0, after), min(100, max(1, limit))),
            )
            .fetchall()
        )
        return {
            "items": [{"event_id": str(row[0]), **json.loads(row[1])} for row in rows],
            "cursor": rows[-1][0] if rows else after,
        }

    def report(self, ident: str) -> dict:
        record = self.get(ident)
        comparison: list[dict[str, Any]] = []
        for experiment in record.experiments:
            metrics: dict[str, Any] = {}
            artifacts: list[dict[str, Any]] = []
            provenance: list[dict[str, Any]] = []
            if experiment.job:
                for stage in experiment.job.stages:
                    result_metrics = stage.result.get("metrics", {})
                    if isinstance(result_metrics, dict):
                        metrics.update(result_metrics)
                    artifacts.extend(a.model_dump(mode="json") for a in stage.outputs)
                    provenance.append(
                        {
                            "stage_id": stage.stage_id,
                            "worker_node_id": stage.worker_node_id,
                            "placement_plan": stage.placement_plan,
                            "started_at": stage.started_at,
                            "ended_at": stage.ended_at,
                            "result": stage.result,
                        }
                    )
            comparison.append(
                {
                    "experiment_id": experiment.experiment_id,
                    "job_id": experiment.job_id,
                    "state": experiment.state,
                    "parameters": experiment.request.model_dump(),
                    "dataset_id": record.objective.dataset_id,
                    "dataset_revision": record.objective.dataset_revision,
                    "metrics": metrics,
                    "artifacts": artifacts,
                    "provenance": provenance,
                    "reserved_wall_seconds": experiment.reserved_wall_seconds,
                    "error_code": experiment.error_code,
                }
            )
        candidates = []
        criterion = record.objective.success_criteria[0]
        for item in comparison:
            score = item["metrics"].get(criterion.metric)
            if (
                item["state"] == "succeeded"
                and isinstance(score, (int, float))
                and not isinstance(score, bool)
            ):
                candidates.append((float(score), item["experiment_id"]))
        candidates.sort(reverse=criterion.direction == "maximize")
        return {
            "supervision": record,
            "comparison": comparison,
            "best_candidate": candidates[0][1] if candidates else None,
            "comparison_metric": criterion.metric,
            "activation_performed": False,
            "limitations": [
                "Candidate ranking uses reported training metrics, not a validated promotion gate.",
                "The evaluation reference is recorded; held-out evaluation is not executed here.",
                "Live metrics and hardware support require evidence reported by the worker.",
                "Reserved wall budgets are not refunded; reconnects cannot create extra work.",
            ],
        }

    def recover(self):
        rows = (
            self.store._conn()
            .execute(
                "SELECT id FROM training_supervisions WHERE state IN ('active','stop_requested')"
            )
            .fetchall()
        )
        for row in rows:
            record = self.get(row[0])
            for experiment in record.experiments:
                if (
                    experiment.state == "reserved"
                    and self.clock() > experiment.created_at + experiment.reserved_wall_seconds
                ):
                    with self._transaction() as conn:
                        current = self._read(record.supervision_id, conn)
                        for item in current.experiments:
                            if (
                                item.experiment_id == experiment.experiment_id
                                and item.state == "reserved"
                            ):
                                item.state = "deadline_exceeded"
                                item.error_code = "preparation_deadline_exceeded"
                                current.current_job_id = None
                                current.state = (
                                    "stopped" if current.state == "stop_requested" else "failed"
                                )
                                current.reason = (
                                    "Interrupted preparation exceeded its reserved deadline"
                                )
                                self._save(current, conn, "preparation_expired")
            if record.state == "stop_requested" and record.current_job_id:
                try:
                    self.jobs.request_cancel(record.current_job_id)
                except JobError as exc:
                    if exc.status_code != 404:
                        raise

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def poll():
            while not self._stop.wait(2):
                try:
                    self.recover()
                except Exception:
                    # An unavailable coordinator leaves its durable reservation
                    # untouched. No new work or model activation is attempted.
                    continue

        self._thread = threading.Thread(target=poll, name="training-supervisor", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
