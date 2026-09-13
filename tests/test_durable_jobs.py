"""Durable scheduling and publication with isolated SQLite and fake worker clocks."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tailcam.config import AppConfig
from tailcam.jobs.models import (
    ArtifactRef,
    JobError,
    JobSpec,
    OutputSlot,
    PreparedOutput,
    Progress,
    ResourceBudget,
    ResultManifest,
    SafeError,
    StageSpec,
)
from tailcam.jobs.service import JobService
from tailcam.node import RoleDisabledError
from tailcam.persistence.store import Store
from tailcam.storage.service import StorageService


@pytest.fixture
def jobs(tmp_path):
    config = AppConfig()
    store = Store(tmp_path / "state.db")
    now = [1000.0]
    service = JobService(config, store, store.get_node_id(), clock=lambda: now[0])
    service.test_now = now
    return service


def request(jobs, **kwargs):
    return JobSpec(task="training", origin_node_id=jobs.node_id, **kwargs)


def submit(jobs, **kwargs):
    return jobs.submit(request(jobs, **kwargs), idempotency_key=str(uuid4()))


def finish(jobs, lease, payload=b"result"):
    slots = lease.spec.outputs
    manifest = ResultManifest(
        outputs=[
            PreparedOutput(
                slot=slot.slot,
                path=slot.slot + ".bin",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            for slot in slots
        ],
        result={"accuracy": 0.9},
    )
    permit = jobs.prepare_result(lease, manifest)
    refs = [
        ArtifactRef(
            artifact_id=slot.artifact_id,
            owner_node_id=jobs.node_id,
            sha256=manifest.outputs[index].sha256,
            size_bytes=len(payload),
            slot=slot.slot,
        )
        for index, slot in enumerate(slots)
    ]
    return jobs.commit_result(permit, refs), permit, refs


def test_idempotency_is_durable_and_conflicting_request_is_refused(jobs):
    spec = request(jobs, parameters={"epochs": 2})
    first = jobs.submit(spec, idempotency_key="one", principal_scope="agent")
    replay = jobs.submit(
        request(jobs, parameters={"epochs": 2}), idempotency_key="one", principal_scope="agent"
    )
    assert replay.job_id == first.job_id
    restarted = JobService(
        jobs.config, Store(jobs.journal.store.db_path), jobs.node_id, clock=jobs.clock
    )
    assert (
        restarted.submit(spec, idempotency_key="one", principal_scope="agent").job_id
        == first.job_id
    )
    with pytest.raises(JobError) as error:
        restarted.submit(
            request(jobs, parameters={"epochs": 3}), idempotency_key="one", principal_scope="agent"
        )
    assert error.value.code == "idempotency_conflict"
    assert len(jobs.list()) == 1


def test_atomic_parallel_submission_obeys_queue_limit(jobs):
    jobs.config.jobs.max_queued = 3
    barrier = threading.Barrier(8)

    def enqueue(_):
        barrier.wait()
        try:
            return submit(jobs).job_id
        except JobError as error:
            assert error.code == "queue_full"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(enqueue, range(8)))
    assert len([x for x in results if x]) == 3


def test_atomic_claim_enforces_training_gpu_and_memory_slots(jobs):
    for _ in range(4):
        submit(jobs, resource_budget=ResourceBudget(gpu_slots=1))
    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = list(pool.map(lambda _: jobs.claim_local(str(uuid4())), range(8)))
    claimed = [lease for lease in leases if lease]
    assert len(claimed) == 1
    finish(jobs, claimed[0])
    assert jobs.claim_local() is not None


def test_priority_places_interactive_work_before_training(jobs):
    low = submit(jobs, priority=90)
    urgent = jobs.submit(
        JobSpec(task="printer_analysis", origin_node_id=jobs.node_id, priority=10),
        idempotency_key="urgent",
    )
    assert jobs.claim_local().job_id == urgent.job_id
    assert jobs.get(low.job_id).state == "queued"


def test_expired_lease_retries_same_slot_and_fences_old_worker(jobs):
    record = submit(jobs, outputs=[OutputSlot(slot="model", kind="model_output")])
    first = jobs.claim_local()
    jobs.start_attempt(first)
    jobs.test_now[0] += 16
    assert jobs.recover() == 1
    jobs.test_now[0] += 3
    second = jobs.claim_local()
    assert second.job_id == first.job_id and second.attempt == 2
    assert second.spec.outputs[0].artifact_id == first.spec.outputs[0].artifact_id
    with pytest.raises(JobError, match="current"):
        jobs.heartbeat(first)
    with pytest.raises(JobError):
        finish(jobs, first)
    result, _, _ = finish(jobs, second)
    assert result.state == "succeeded"
    assert jobs.get(record.job_id).stages[0].attempt == 2


def test_publication_fence_survives_restart_and_deadline(jobs):
    record = submit(jobs, outputs=[OutputSlot(slot="model", kind="model_output")])
    lease = jobs.claim_local()
    manifest = ResultManifest(
        outputs=[
            PreparedOutput(
                slot="model",
                path="weights.pt",
                size_bytes=2,
                sha256=hashlib.sha256(b"ok").hexdigest(),
            )
        ]
    )
    permit = jobs.prepare_result(lease, manifest)
    jobs.test_now[0] += 4000
    restarted = JobService(
        jobs.config, Store(jobs.journal.store.db_path), jobs.node_id, clock=jobs.clock
    )
    assert restarted.recover() == 0
    assert restarted.claim_local() is None
    assert restarted.prepare_result(lease, manifest) == permit
    assert restarted.pending_publications() == [permit]
    refs = [
        ArtifactRef(
            artifact_id=permit.slots[0].artifact_id,
            owner_node_id=jobs.node_id,
            sha256=manifest.outputs[0].sha256,
            size_bytes=2,
            slot="model",
        )
    ]
    result = restarted.commit_result(permit, refs)
    assert result.state == "succeeded"
    assert restarted.commit_result(permit, refs) == result
    assert restarted.pending_publications() == []
    assert restarted.get(record.job_id).stages[0].outputs == refs


def test_changed_publication_and_unverified_artifacts_are_refused(jobs):
    submit(jobs, outputs=[OutputSlot(slot="model", kind="model_output")])
    lease = jobs.claim_local()
    manifest = ResultManifest(
        outputs=[PreparedOutput(slot="model", path="weights.pt", size_bytes=2, sha256="a" * 64)]
    )
    permit = jobs.prepare_result(lease, manifest)
    with pytest.raises(JobError):
        jobs.prepare_result(lease, manifest.model_copy(update={"result": {"changed": True}}))
    with pytest.raises(JobError):
        jobs.commit_result(
            permit,
            [
                ArtifactRef(
                    artifact_id=str(uuid4()),
                    owner_node_id=jobs.node_id,
                    sha256="a" * 64,
                    size_bytes=2,
                    slot="model",
                )
            ],
        )


def test_stage_retry_does_not_reexecute_completed_stage(jobs):
    record = submit(
        jobs,
        stages=[
            StageSpec(stage_id="prepare", task="labeling"),
            StageSpec(stage_id="train", task="training", depends_on=["prepare"]),
        ],
    )
    first = jobs.claim_local()
    assert first.stage_id == "prepare"
    finish(jobs, first)
    second = jobs.claim_local()
    assert second.stage_id == "train"
    jobs.fail(second, SafeError(code="engine_error", detail="Known failure"))
    jobs.retry(record.job_id)
    retry = jobs.claim_local()
    assert retry.stage_id == "train" and retry.attempt == 2
    finish(jobs, retry)
    assert [s.attempt for s in jobs.get(record.job_id).stages] == [1, 2]


def test_queued_and_running_cancel_are_distinct(jobs):
    waiting = submit(jobs)
    assert jobs.request_cancel(waiting.job_id).state == "cancelled"
    active = submit(jobs)
    lease = jobs.claim_local()
    jobs.start_attempt(lease)
    assert jobs.request_cancel(active.job_id).state == "cancel_requested"
    assert jobs.is_cancel_requested(lease)
    jobs.heartbeat(lease)  # Health remains observable until the child confirms exit.
    with pytest.raises(JobError):
        jobs.prepare_result(lease, ResultManifest())
    assert (
        jobs.fail(lease, SafeError(code="cancelled", detail="Child stopped")).state == "cancelled"
    )


def test_deadline_and_retry_budgets_terminate(jobs):
    record = submit(jobs, max_attempts=1, deadline_at=1002)
    lease = jobs.claim_local()
    jobs.test_now[0] = 1003
    jobs.recover()
    assert jobs.get(record.job_id).state == "deadline_exceeded"
    with pytest.raises(JobError):
        jobs.retry(record.job_id)
    with pytest.raises(JobError):
        jobs.heartbeat(lease)


def test_stage_deadline_does_not_silently_start_another_attempt(jobs):
    record = submit(
        jobs,
        deadline_at=1100,
        stages=[StageSpec(task="training", budget=ResourceBudget(wall_seconds=2))],
    )
    lease = jobs.claim_local()
    assert lease.deadline_at == 1002
    jobs.test_now[0] = 1003
    jobs.recover()
    assert jobs.get(record.job_id).state == "deadline_exceeded"
    assert jobs.claim_local() is None


def test_publication_keeps_original_source_identity(jobs):
    origin = str(uuid4())
    jobs.submit(JobSpec(task="training", origin_node_id=origin), idempotency_key="origin")
    _, permit, _ = finish(jobs, jobs.claim_local())
    assert permit.origin_node_id == origin


@pytest.mark.parametrize("metric", [float("nan"), float("inf"), float("-inf")])
def test_progress_cannot_persist_nonfinite_metrics(metric):
    with pytest.raises(ValueError):
        Progress(metrics={"loss": metric})


def test_unknown_failure_never_automatically_retries(jobs):
    submit(jobs)
    lease = jobs.claim_local()
    result = jobs.fail(
        lease, SafeError(code="unknown", detail="Stopped for investigation", retryable=True)
    )
    assert result.state == "failed"
    assert jobs.claim_local() is None


def test_status_and_constructor_do_not_start_execution(jobs):
    submit(jobs)
    jobs.get(jobs.list()[0].job_id)
    assert jobs._thread is None and jobs._running == {}


def test_real_queue_callback_is_bounded_and_stops(jobs):
    completed = threading.Event()

    class Executor:
        def execute(self, lease, service):
            service.start_attempt(lease)
            service.heartbeat(lease, Progress(epoch=1))
            finish(service, lease)
            completed.set()

    jobs.executor = Executor()
    record = submit(jobs)
    jobs.start()
    try:
        assert completed.wait(3)
        assert jobs.get(record.job_id).state == "succeeded"
    finally:
        jobs.close()
    assert not jobs._thread.is_alive()


def test_role_disabled_worker_refuses_without_queue_record(tmp_path):
    config = AppConfig()
    config.node.roles = []
    store = Store(tmp_path / "state.db")
    jobs = JobService(config, store, store.get_node_id())
    with pytest.raises(RoleDisabledError):
        submit(jobs)
    assert jobs.list() == []


def test_live_session_and_training_share_gpu_and_memory_admission(jobs):
    budget = ResourceBudget(gpu_slots=1)
    jobs.reserve_live("camera-one", budget)
    jobs.reserve_live("camera-one", budget)
    assert len(jobs.live_usage()) == 1
    submit(jobs, resource_budget=budget)
    assert jobs.claim_local() is None
    with pytest.raises(JobError):
        jobs.reserve_live("camera-two", budget)
    jobs.release_live("camera-one")
    assert jobs.claim_local() is not None
    with pytest.raises(JobError):
        jobs.reserve_live("camera-two", budget)


def test_unsupported_hard_budget_request_fails_before_queue(jobs):
    for update in ({"require_hard_workspace_limit": True}, {"require_hard_memory_limit": True}):
        with pytest.raises(JobError):
            submit(jobs, resource_budget=ResourceBudget(**update))
    assert jobs.list() == []


def test_supervision_stop_fences_late_submit_and_claim(jobs):
    jobs.journal.connection.executescript(
        "CREATE TABLE training_supervisions(id TEXT,state TEXT);"
        "CREATE TABLE training_experiments(supervision TEXT,job TEXT);"
    )
    spec = request(jobs, reference={"supervision_id": "supervisor"})
    with pytest.raises(JobError) as error:
        jobs.submit(spec, idempotency_key="experiment")
    assert error.value.code == "supervision_stopped"
    with jobs.journal.transaction() as conn:
        conn.execute("INSERT INTO training_supervisions VALUES('supervisor','active')")
        conn.execute("INSERT INTO training_experiments VALUES('supervisor',?)", (spec.job_id,))
    record = jobs.submit(spec, idempotency_key="experiment")
    with jobs.journal.transaction() as conn:
        conn.execute("UPDATE training_supervisions SET state='stop_requested'")
    assert jobs.claim_local() is None
    assert jobs.get(record.job_id).state == "cancelled"


def test_storage_workspace_and_output_plans_survive_later_policy_edits(jobs, tmp_path):
    storage = StorageService(jobs.config, jobs.journal.store, jobs.node_id)
    storage.register_location(str(tmp_path / "media"), create=True)
    jobs.storage = storage
    record = submit(jobs, outputs=[OutputSlot(slot="model", kind="model_output")])
    original = jobs.spec(record.job_id).stages[0]
    policy = storage.get_policy()
    policy.zero_local_media = True
    storage.set_policy(policy)
    lease = jobs.claim_local()
    assert lease.spec.outputs == original.outputs
    with storage.workspace_for_task(
        "training",
        max_bytes=lease.spec.budget.workspace_bytes,
        admission=lease.spec.workspace_admission,
    ):
        pass
