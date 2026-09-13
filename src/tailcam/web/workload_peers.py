"""Bounded worker inventory over the same approved, persistent node identities as storage."""

from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from tailcam import hostinfo
from tailcam.jobs.models import RESERVED_TASKS, TASK_KINDS, TaskAvailability, WorkerInfo


class WorkerDirectory:
    def __init__(self, ctx):
        self.ctx = ctx
        self._lock = threading.Lock()
        self._refreshed = 0.0
        self._cache: dict[str, WorkerInfo] = {}

    def local(self) -> WorkerInfo:
        ctx, profile = self.ctx, hostinfo.profile()
        config = ctx.config.jobs
        states = dict(
            ctx.store._conn()
            .execute("SELECT state,count(*) FROM workload_jobs GROUP BY state")
            .fetchall()
        )
        now = time.time()
        recent_stages = [
            json.loads(row[0])
            for row in ctx.store._conn().execute(
                "SELECT stage.value FROM workload_jobs "
                "JOIN json_each(workload_jobs.data,'$.stages') stage "
                "WHERE json_extract(stage.value,'$.state')='succeeded' "
                "AND json_extract(stage.value,'$.worker_node_id')=? "
                "AND json_extract(stage.value,'$.placement_plan.selected_target.provider_id') "
                "IS NULL "
                "AND json_extract(stage.value,'$.ended_at')>?",
                (ctx.node_id, now - 1800),
            )
        ]
        recent = {stage["task"] for stage in recent_stages}
        gpu_observed = any(
            stage.get("result", {}).get("device") in {"cuda", "mps"} for stage in recent_stages
        )
        workloads = getattr(ctx, "workloads", None)
        observation = workloads.local_live_observation() if workloads is not None else {}
        completed_at = observation.get("completed_at")
        if (
            observation.get("worker_node_id") == ctx.node_id
            and isinstance(completed_at, (int, float))
            and not isinstance(completed_at, bool)
            and math.isfinite(completed_at)
            and 0 <= now - completed_at < 1800
        ):
            recent.add("live_detection")
            gpu_observed = gpu_observed or observation.get("device") in {"cuda", "mps"}
        tasks = []
        for task in TASK_KINDS:
            role = "training" if task == "training" else "analysis"
            if task in RESERVED_TASKS:
                item = TaskAvailability(
                    task=task,
                    state="unavailable",
                    code="reserved_task",
                    detail="Reserved for a later release",
                )
            elif not ctx.has_role(role):
                item = TaskAvailability(
                    task=task,
                    state="disabled",
                    code="role_disabled",
                    detail=f"The {role} role is disabled",
                )
            elif task in recent:
                item = TaskAvailability(
                    task=task,
                    state="ready",
                    code="recent_execution",
                    detail="A task of this kind completed in the last 30 minutes",
                )
            else:
                item = TaskAvailability(task=task)
            tasks.append(item)
        storage_policy = ctx.storage_service.get_policy()
        workspace = min(config.max_workspace_bytes, storage_policy.workspace_max_bytes)
        if storage_policy.zero_local_media:
            workspace = 0
        return WorkerInfo(
            node_id=ctx.node_id,
            name=ctx.config.node.name or ctx.local_host,
            online=True,
            roles=list(ctx.active_roles),
            tasks=tasks,
            queued=sum(
                states.get(state, 0) for state in ("queued", "retry_wait", "waiting_for_worker")
            ),
            running=sum(
                states.get(state, 0)
                for state in ("leased", "running", "committing", "cancel_requested")
            )
            + len(ctx.jobs.live_usage()),
            cpu_threads=min(config.max_cpu_threads, profile.cpu_count),
            memory_bytes=min(config.max_memory_bytes, profile.total_ram_bytes // 2)
            if profile.total_ram_bytes
            else config.max_memory_bytes,
            workspace_bytes=workspace,
            gpu_slots=config.max_gpu_slots,
            gpu_observed=gpu_observed,
            latency_ms=0,
        )

    def workers(self) -> list[WorkerInfo]:
        with self._lock:
            if time.monotonic() - self._refreshed < 15:
                return [self.local(), *self._cache.values()]
            nodes = self.ctx.storage_peers.refresh()
            approved = {item["node_id"] for item in nodes if item.get("node_id")}
            previous = {
                node_id: item.model_copy(update={"online": False})
                for node_id, item in self._cache.items()
                if node_id in approved
            }

            def inspect(item):
                node_id = item.get("node_id")
                if not node_id or not item.get("online"):
                    return None
                try:
                    started = time.monotonic()
                    payload = self.ctx.job_transport.request(
                        node_id, "GET", "/api/v1/workloads/worker"
                    )
                    worker = WorkerInfo.model_validate(payload)
                    if worker.node_id != node_id:
                        return None
                    worker.latency_ms = (time.monotonic() - started) * 1000
                    return worker
                except Exception:
                    return None

            with ThreadPoolExecutor(max_workers=4) as pool:
                for worker in pool.map(inspect, nodes[:32]):
                    if worker is not None and worker.node_id != self.ctx.node_id:
                        previous[worker.node_id] = worker
            self._cache, self._refreshed = previous, time.monotonic()
            return [self.local(), *previous.values()]
