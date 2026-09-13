"""Revisioned manual/approved-auto placement with no implicit execution fallback."""

from __future__ import annotations

from collections.abc import Callable

from tailcam.jobs.models import (
    RESERVED_TASKS,
    JobError,
    PlacementPlan,
    PlacementPolicy,
    ProviderDefinition,
    ResourceBudget,
    TaskKind,
    TaskRoute,
    WorkerInfo,
    WorkerTarget,
)
from tailcam.jobs.store import JobStore


class PlacementService:
    def __init__(
        self,
        config,
        journal: JobStore,
        node_id: str,
        *,
        workers: Callable[[], list[WorkerInfo]] | None = None,
    ):
        self.config, self.journal, self.node_id = config, journal, node_id
        self._workers = workers or (lambda: [])

    def get_policy(self) -> PlacementPolicy:
        return PlacementPolicy.model_validate(
            self.journal.setting("placement_policy", self.config.jobs.policy or {})
        )

    def set_policy(
        self, policy: PlacementPolicy | dict, *, expected_revision: int
    ) -> PlacementPolicy:
        policy = PlacementPolicy.model_validate(policy).model_copy(deep=True)
        with self.journal.transaction() as conn:
            current = self.get_policy()
            if current.revision != expected_revision:
                raise JobError("policy_conflict", "Placement policy changed; reload before saving.")
            policy.revision = current.revision + 1
            self.journal.set_setting("placement_policy", policy.model_dump(mode="json"), conn)
        self.config.jobs.policy = policy.model_dump(mode="json", exclude_none=True)
        return policy

    def providers(self) -> list[ProviderDefinition]:
        return [
            ProviderDefinition.model_validate_json(row[0])
            for row in self.journal.connection.execute(
                "SELECT data FROM workload_providers ORDER BY id"
            )
        ]

    def provider(self, provider_id: str) -> ProviderDefinition:
        row = self.journal.connection.execute(
            "SELECT data FROM workload_providers WHERE id=?", (provider_id,)
        ).fetchone()
        if row is None:
            raise JobError("provider_missing", "Configured provider does not exist.", 404)
        return ProviderDefinition.model_validate_json(row[0])

    def register_provider(self, provider: ProviderDefinition | dict) -> ProviderDefinition:
        provider = ProviderDefinition.model_validate(provider)
        with self.journal.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workload_providers VALUES(?,?)",
                (provider.provider_id, provider.model_dump_json()),
            )
        return provider

    def delete_provider(self, provider_id: str) -> bool:
        # Existing jobs retain their snapshot; administrative deletion changes later admission.
        with self.journal.transaction() as conn:
            return bool(
                conn.execute("DELETE FROM workload_providers WHERE id=?", (provider_id,)).rowcount
            )

    def workers(self) -> list[WorkerInfo]:
        result = [WorkerInfo.model_validate(item) for item in self._workers()]
        ids = [item.node_id for item in result]
        if len(ids) != len(set(ids)):
            raise JobError("identity_conflict", "Worker identities are ambiguous.", 503)
        return result[:32]

    def plan(
        self,
        task: TaskKind,
        *,
        target: WorkerTarget | None = None,
        budget: ResourceBudget | None = None,
        policy_snapshot: PlacementPolicy | None = None,
    ) -> PlacementPlan:
        if task in RESERVED_TASKS:
            raise JobError("task_reserved", "This task is reserved for a later release.", 422)
        policy = policy_snapshot or self.get_policy()
        route = policy.routes.get(task, TaskRoute(target=WorkerTarget(node_id=self.node_id)))
        selected_budget = (budget or route.budget).model_copy(deep=True)
        mode = "manual" if target is not None else route.mode
        requested = target or route.target or WorkerTarget(node_id=self.node_id)
        selected = requested
        reason = "Manual worker selection"
        workers = {worker.node_id: worker for worker in self.workers()}
        if mode == "auto":
            candidates = [
                worker
                for node_id, worker in workers.items()
                if node_id in route.approved_node_ids
                and self._eligible(worker, task, selected_budget, auto=True)
            ]
            if candidates:
                chosen = min(
                    candidates,
                    key=lambda worker: (
                        worker.running,
                        worker.queued,
                        worker.latency_ms if worker.latency_ms is not None else 1e9,
                        worker.node_id,
                    ),
                )
                selected = WorkerTarget(node_id=chosen.node_id, model=requested.model)
                reason = (
                    "Auto selected an approved capable worker by running work, queue and latency"
                )
            else:
                selected = self._fallback(route, workers, task, selected_budget)
                reason = "Approved Auto workers unavailable; selected an explicit fallback"
        else:
            selected = self._available_target(requested, workers, task, selected_budget)
            if selected is None:
                selected = self._fallback(route, workers, task, selected_budget)
                reason = "Requested worker unavailable; selected an explicit fallback"
        return PlacementPlan(
            task=task,
            policy_revision=policy.revision,
            mode=mode,
            requested_target=requested,
            selected_target=selected,
            fallback_targets=route.fallback_targets,
            reason=reason,
            budget=selected_budget,
        )

    def _fallback(self, route, workers, task, budget):
        for target in route.fallback_targets:
            selected = self._available_target(target, workers, task, budget)
            if selected is not None:
                return selected
        raise JobError("worker_unavailable", "No permitted worker can admit this task.", 503)

    def _available_target(self, target, workers, task, budget):
        if target.provider_id:
            try:
                provider = self.provider(target.provider_id)
            except JobError:
                return None
            if not provider.enabled or task not in provider.tasks:
                return None
            return target.model_copy(
                update={
                    "node_id": target.node_id or self.node_id,
                    "model": target.model or provider.model,
                }
            )
        if target.node_id == self.node_id and target.node_id not in workers:
            # Local role/budget admission is authoritative in JobService.
            return target
        worker = workers.get(target.node_id or "")
        return target if worker and self._eligible(worker, task, budget, auto=False) else None

    @staticmethod
    def _eligible(
        worker: WorkerInfo, task: TaskKind, budget: ResourceBudget, *, auto: bool
    ) -> bool:
        role = "training" if task == "training" else "analysis"
        availability = next((item for item in worker.tasks if item.task == task), None)
        return bool(
            worker.online
            and role in worker.roles
            and availability
            and availability.state in ({"ready"} if auto else {"ready", "unchecked"})
            and worker.cpu_threads >= budget.cpu_threads
            and worker.memory_bytes >= budget.memory_bytes
            and worker.workspace_bytes >= budget.workspace_bytes
            and worker.gpu_slots >= budget.gpu_slots
            and (not auto or budget.gpu_slots == 0 or worker.gpu_observed)
        )
