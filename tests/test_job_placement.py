"""Manual authority, approved Auto, provider validation and frozen route revisions."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from tailcam.config import AppConfig
from tailcam.jobs.models import (
    JobError,
    JobSpec,
    PlacementPolicy,
    ProviderDefinition,
    TaskAvailability,
    TaskRoute,
    WorkerInfo,
    WorkerTarget,
)
from tailcam.jobs.service import JobService
from tailcam.persistence.store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "state.db")
    jobs = JobService(AppConfig(), store, store.get_node_id())
    jobs.placement.test_workers = []
    jobs.placement._workers = lambda: jobs.placement.test_workers
    return jobs


def worker(*, state="ready", **kwargs):
    return WorkerInfo(
        node_id=str(uuid4()),
        online=True,
        roles=["training", "analysis"],
        tasks=[TaskAvailability(task="training", state=state)],
        cpu_threads=4,
        memory_bytes=4 * 1024**3,
        workspace_bytes=1024**3,
        gpu_slots=1,
        **kwargs,
    )


def test_auto_never_selects_unapproved_or_unchecked_worker(service):
    unapproved, unchecked, approved = worker(), worker(state="unchecked"), worker(queued=3)
    service.placement.test_workers = [unapproved, unchecked, approved]
    policy = PlacementPolicy(
        routes={
            "training": TaskRoute(
                mode="auto", approved_node_ids=[unchecked.node_id, approved.node_id]
            )
        }
    )
    service.set_policy(policy, expected_revision=1)
    plan = service.placement.plan("training")
    assert plan.selected_target.node_id == approved.node_id


def test_manual_worker_beats_idle_auto_node_and_records_explicit_fallback(service):
    busy, idle = worker(queued=5), worker()
    service.placement.test_workers = [busy, idle]
    route = TaskRoute(
        target=WorkerTarget(node_id=busy.node_id),
        fallback_targets=[WorkerTarget(node_id=idle.node_id)],
    )
    service.set_policy(PlacementPolicy(routes={"training": route}), expected_revision=1)
    assert service.placement.plan("training").selected_target.node_id == busy.node_id
    busy.online = False
    plan = service.placement.plan("training")
    assert plan.requested_target.node_id == busy.node_id
    assert plan.selected_target.node_id == idle.node_id
    assert "fallback" in plan.reason


def test_no_implicit_fallback_for_offline_manually_pinned_worker(service):
    selected = worker()
    service.placement.test_workers = [selected, worker()]
    service.set_policy(
        PlacementPolicy(
            routes={"training": TaskRoute(target=WorkerTarget(node_id=selected.node_id))}
        ),
        expected_revision=1,
    )
    selected.online = False
    with pytest.raises(JobError):
        service.placement.plan("training")


def test_later_policy_does_not_move_queued_job(service):
    first, second = worker(), worker()
    service.placement.test_workers = [first, second]
    original = service.set_policy(
        PlacementPolicy(routes={"training": TaskRoute(target=WorkerTarget(node_id=first.node_id))}),
        expected_revision=1,
    )
    job = service.submit(
        JobSpec(task="training", origin_node_id=service.node_id), idempotency_key="job"
    )
    service.set_policy(
        PlacementPolicy(
            routes={"training": TaskRoute(target=WorkerTarget(node_id=second.node_id))}
        ),
        expected_revision=original.revision,
    )
    lease = service._claim("coordinator", remote=True)
    assert lease.worker_node_id == first.node_id
    assert service.get(job.job_id).actual_target.node_id == first.node_id


@pytest.mark.parametrize(
    "url",
    [
        "file:///private",
        "http://user:secret@host",
        "http://host?secret=1",
        "http://host/path",
        "ftp://host",
    ],
)
def test_provider_urls_reject_credentials_arbitrary_paths_and_protocols(url):
    with pytest.raises(ValidationError):
        ProviderDefinition(name="model", base_url=url, model="vision")


def test_provider_registration_is_passive_and_freezes_model_config(service):
    provider = service.placement.register_provider(
        ProviderDefinition(name="Vision", base_url="http://127.0.0.1:1", model="vision")
    )
    route = TaskRoute(target=WorkerTarget(provider_id=provider.provider_id))
    service.set_policy(PlacementPolicy(routes={"printer_analysis": route}), expected_revision=1)
    job = service.submit(
        JobSpec(task="printer_analysis", origin_node_id=service.node_id), idempotency_key="print"
    )
    service.placement.delete_provider(provider.provider_id)
    assert service.spec(job.job_id).stages[0].parameters["_provider"]["model"] == "vision"
    assert service._thread is None


def test_reserved_tasks_and_cyclic_stages_are_refused(service):
    with pytest.raises(JobError):
        service.placement.plan("speech_synthesis")
    with pytest.raises(JobError):
        service.submit(
            JobSpec(
                task="training",
                origin_node_id=service.node_id,
                stages=[
                    {"stage_id": "later", "task": "training", "depends_on": ["earlier"]},
                    {"stage_id": "earlier", "task": "training"},
                ],
            ),
            idempotency_key="cycle",
        )
