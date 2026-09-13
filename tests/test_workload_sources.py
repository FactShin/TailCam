"""Source adapters, immutable exports and bounded worker-only provisioning."""

from __future__ import annotations

import base64
import hashlib
import threading
import time
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import cv2
import httpx
import numpy as np
import pytest

from tailcam.config import AppConfig
from tailcam.jobs.models import (
    JobError,
    JobSpec,
    PlacementPlan,
    ResourceBudget,
    WorkerTarget,
)
from tailcam.jobs.service import JobService
from tailcam.persistence.models import DatasetRecord, DatasetSampleRecord, ModelRecord
from tailcam.storage.service import StorageService
from tailcam.workloads import handlers
from tailcam.workloads.executor import ProcessExecutor
from tailcam.workloads.live import LiveRequest, WorkloadService
from tailcam.workloads.process import ExecutionError
from tailcam.workloads.training import dataset_revision, prepare_training_job, project_job


def setup_storage(store, tmp_path):
    config = AppConfig()
    storage = StorageService(config, store, store.get_node_id())
    storage.register_location(str(tmp_path / "canonical"), create=True, make_default=True)
    jobs = JobService(config, store, storage.node_id, storage)
    return config, storage, jobs


def test_builtin_download_is_bounded_and_worker_offline_is_default(tmp_path, monkeypatch):
    with pytest.raises(ExecutionError, match="offline"):
        handlers._provision_builtin("yolo11n", tmp_path, 64_000_000)
    assert not list(tmp_path.iterdir())
    monkeypatch.delenv("TAILCAM_WORKER_OFFLINE")
    calls = []
    payload = b"w" * 1_000_000

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=payload)

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(respond))
    )
    inputs = handlers._provision_builtin("yolo11n", tmp_path, 64_000_000)
    assert inputs == [{"slot": "model", "path": "yolo11n.pt"}]
    assert (tmp_path / "yolo11n.pt").read_bytes() == payload
    assert calls == ["https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt"]
    handlers._provision_builtin("yolo11n", tmp_path, 64_000_000)
    assert len(calls) == 1  # reuse within the persistent child


def test_builtin_oversized_response_is_removed(tmp_path, monkeypatch):
    monkeypatch.delenv("TAILCAM_WORKER_OFFLINE")
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            **kw,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 16_000_001)),
        ),
    )
    with pytest.raises(ExecutionError, match="provisioning failed"):
        handlers._provision_builtin("yolo11n", tmp_path, 64_000_000)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_requested_accelerator_never_silently_uses_cpu(tmp_path, monkeypatch, device):
    monkeypatch.setattr("tailcam.training.engine.torch_device", lambda: "cpu")
    with pytest.raises(ExecutionError, match="accelerator.*unavailable"):
        handlers.run_handler(
            {
                "task": "training",
                "parameters": {"device": device},
                "gpu_slots": 1,
                "workspace_bytes": 1024,
            },
            tmp_path,
            lambda _: None,
        )


def test_cpu_budget_does_not_probe_or_use_accelerator(tmp_path, monkeypatch):
    monkeypatch.setattr("tailcam.training.engine.torch_device", lambda: pytest.fail("GPU probe"))
    captured = []
    monkeypatch.setattr(
        handlers, "_train", lambda params, *args: (captured.append(params) or {}, {})
    )
    result = handlers.run_handler(
        {
            "task": "training",
            "parameters": {},
            "gpu_slots": 0,
            "workspace_bytes": 1024,
            "output_bytes": 1024,
        },
        tmp_path,
        lambda _: None,
    )
    assert captured[0]["device"] == result["result"]["device"] == "cpu"


def test_abandoned_attempt_releases_workspace_even_when_fenced(store, tmp_path, monkeypatch):
    config, storage, jobs = setup_storage(store, tmp_path)
    job = jobs.submit(
        JobSpec(task="timelapse_encode", origin_node_id=storage.node_id), idempotency_key="cleanup"
    )
    lease = jobs.claim_local()
    executor = ProcessExecutor(storage, node_id=storage.node_id)
    monkeypatch.setattr(
        executor, "run", lambda *args: (_ for _ in ()).throw(ExecutionError("cancelled", "stopped"))
    )
    monkeypatch.setattr(
        jobs,
        "fail",
        lambda *args: (_ for _ in ()).throw(JobError("stale_lease", "already cancelled")),
    )
    with pytest.raises(JobError, match="already cancelled"):
        executor.execute(lease, jobs)
    assert (
        storage.catalog.connection.execute(
            "SELECT COUNT(*) FROM storage_reservations WHERE category='workspace'"
        ).fetchone()[0]
        == 0
    )
    assert storage.catalog.setting(f"job_workspace:{lease.attempt_id}") == ""
    assert jobs.get(job.job_id) is not None


def test_training_snapshot_freezes_subset_and_legacy_weights_without_enabling_policy(
    store, tmp_path, monkeypatch
):
    config, storage, jobs = setup_storage(store, tmp_path)
    training = SimpleNamespace(
        _store=store, _storage_service=storage, _notify_run=lambda *a, **k: None
    )
    dataset = store.add_dataset(DatasetRecord(None, "Reviewed", "classification", time.time()))
    for index, label in enumerate(("person", "person", "vehicle", "vehicle", "unapproved")):
        path = tmp_path / (label + str(index) + ".jpg")
        cv2.imwrite(str(path), np.zeros((8, 8, 3), np.uint8))
        store.add_sample(
            DatasetSampleRecord(
                None, dataset, str(path), None, label, "manual", "cam0", "source", time.time()
            )
        )
    weights = tmp_path / "registered.pt"
    weights.write_bytes(b"already provisioned weights")
    model = store.add_model(
        ModelRecord(None, "Approved", "byo", str(weights), "[]", "base", "{}", time.time())
    )
    revision = dataset_revision(training, dataset)
    params = dict(
        dataset_id=dataset,
        dataset_revision=revision,
        base_model_id=model,
        epochs=2,
        image_size=224,
        seed=47,
        worker_node_id=storage.node_id,
        camera_ids=["cam0"],
        classes=["person", "vehicle"],
        job_id=str(uuid4()),
        budget=ResourceBudget(),
    )
    spec = prepare_training_job(training, **params)
    again = prepare_training_job(training, **params)
    assert spec == again and len(store.list_runs()) == 1
    assert storage.enabled is False and config.storage.unified_enabled is False
    assert storage.catalog.resolve_alias("model", str(model), "file") is not None
    assert dataset_revision(training, dataset) == revision
    export = next(ref for ref in spec.input_artifacts if ref.slot == "dataset")
    with zipfile.ZipFile(storage.resolve(export.artifact_id)) as archive:
        assert all("unapproved" not in name for name in archive.namelist())
        assert {name.split("/")[1] for name in archive.namelist()} == {"person", "vehicle"}
    assert spec.parameters["seed"] == 47 and spec.parameters["classes"] == ["person", "vehicle"]
    assert spec.placement_plan.selected_target.node_id == params["worker_node_id"]
    # Exercise the durable source -> isolated-runtime boundary -> publication ->
    # legacy projection using a fake ML runtime, never an installed model library.
    record = jobs.submit(spec, idempotency_key=spec.job_id)
    lease = jobs.claim_local()
    executor = ProcessExecutor(storage, node_id=storage.node_id)

    def runtime(request, root, **kwargs):
        assert request["parameters"]["seed"] == 47
        assert {item["slot"] for item in request["inputs"]} == {"model", "dataset"}
        payload = b"trained fixture weights"
        (root / "trained.pt").write_bytes(payload)
        return {
            "result": {
                "classes": ["person", "vehicle"],
                "metrics": {"accuracy": 0.9},
                "epochs": 2,
                "backend": "yolo",
                "device": "cpu",
            },
            "outputs": [
                {
                    "slot": "model",
                    "path": "trained.pt",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }

    monkeypatch.setattr(executor.runner, "run", runtime)
    executor.execute(lease, jobs)
    finished = jobs.get(record.job_id)
    assert finished.state == "succeeded"
    project_job(training, finished)
    run = store.get_run(int(spec.reference["training_run_id"]))
    assert run.status == "complete" and run.epoch == 2
    trained = store.get_model(run.model_id)
    assert trained.active == 0
    artifact = storage.catalog.resolve_alias("model", str(trained.id), "file")
    assert storage.resolve(artifact.artifact_id).read_bytes() == b"trained fixture weights"
    sample = store.list_samples(dataset)[0]
    store.set_sample_label(sample.id, "changed")
    with pytest.raises(JobError, match="changed since"):
        prepare_training_job(training, **{**params, "job_id": str(uuid4())})


def test_legacy_remote_placement_is_preserved_without_local_fallback(store, tmp_path):
    config, storage, jobs = setup_storage(store, tmp_path)
    config.detection.node = "saved-camera-node"
    target = str(uuid4())
    calls = []
    jobs.placement.plan = lambda task, **kw: calls.append((task, kw)) or kw["target"]
    workloads = WorkloadService(jobs, storage, store, config, resolve_legacy_node=lambda _: target)
    assert workloads._plan("live_detection").node_id == target
    workloads.resolve_legacy_node = lambda _: None
    with pytest.raises(JobError, match="unavailable or unapproved"):
        workloads._plan("live_detection")
    assert len(calls) == 1


def live_request(identity, plan, *, session=None):
    ok, encoded = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return LiveRequest(
        session_id=session or str(uuid4()),
        request_id=str(uuid4()),
        coordinator_node_id=identity,
        target_node_id=identity,
        plan=plan,
        jpeg=base64.b64encode(encoded).decode(),
        deadline_at=time.time() + 5,
    )


def test_live_queue_retains_only_newest_pending_frame_and_releases_resources(
    store, tmp_path, monkeypatch
):
    config, storage, jobs = setup_storage(store, tmp_path)
    workloads = WorkloadService(jobs, storage, store, config)
    plan = jobs.placement.plan("live_detection")
    first = live_request(storage.node_id, plan)
    entered, resume = threading.Event(), threading.Event()
    seen = []
    closed = []

    class Actor:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, identity, *args, **kwargs):
            seen.append(identity)
            if len(seen) == 1:
                entered.set()
                assert resume.wait(3)
            return {"available": True, "outcome": "succeeded", "predictions": [{"boxes": []}]}

        def close(self):
            closed.append(True)

    monkeypatch.setattr("tailcam.workloads.live.ProcessActor", Actor)
    monkeypatch.setattr(workloads, "_model_inputs", lambda *args: ({"_model_name": "fixture"}, []))
    results = {}

    def run(request):
        try:
            results[request.request_id] = workloads.execute_live(request)
        except JobError as exc:
            results[request.request_id] = exc.code

    threads = [threading.Thread(target=run, args=(first,))]
    threads[0].start()
    assert entered.wait(3)
    second = live_request(storage.node_id, plan, session=first.session_id)
    third = live_request(storage.node_id, plan, session=first.session_id)
    threads.append(threading.Thread(target=run, args=(second,)))
    threads[1].start()
    end = time.monotonic() + 3
    while time.monotonic() < end:
        with workloads._lock:
            if workloads._sessions[first.session_id].pending is not None:
                break
        time.sleep(0.01)
    threads.append(threading.Thread(target=run, args=(third,)))
    threads[2].start()
    threads[1].join(3)
    assert results[second.request_id] == "superseded"
    resume.set()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert seen == [first.request_id, third.request_id]
    for request in (first, third):
        result = results[request.request_id]
        workloads._validate_live_result(result, request)
        assert result["predictions"][0]["boxes"] == []
    workloads.close()
    assert closed and not jobs._live_budgets
    assert (
        storage.catalog.connection.execute(
            "SELECT COUNT(*) FROM storage_reservations WHERE category='workspace'"
        ).fetchone()[0]
        == 0
    )


def test_live_response_rejects_wrong_worker_and_nonfinite_boxes():
    identity = str(uuid4())
    plan = PlacementPlan(
        task="live_detection",
        requested_target=WorkerTarget(node_id=identity),
        selected_target=WorkerTarget(node_id=identity),
    )
    request = live_request(identity, plan)
    result = {
        "available": True,
        "outcome": "succeeded",
        "worker_node_id": str(uuid4()),
        "session_id": request.session_id,
        "model": "x",
        "execution_ms": 1,
        "queue_ms": 0,
        "predictions": [{"boxes": []}],
    }
    with pytest.raises(JobError, match="mismatched"):
        WorkloadService._validate_live_result(result, request)
    result["worker_node_id"] = identity
    result["predictions"][0]["boxes"] = [
        {"label": "person", "confidence": float("nan"), "cx": 0.5, "cy": 0.5, "w": 0.4, "h": 0.4}
    ]
    with pytest.raises(JobError, match="invalid"):
        WorkloadService._validate_live_result(result, request)


def test_successful_builtin_worker_cache_survives_session_cleanup(store, tmp_path, monkeypatch):
    from tailcam.ai.detector import builtin_models_dir

    config, storage, jobs = setup_storage(store, tmp_path)
    config.detection.engine = "ultralytics"
    workloads = WorkloadService(jobs, storage, store, config)
    plan = jobs.placement.plan("live_detection")
    provisioned = []
    roots = []
    payload = b"fixed built-in test weights" * 50000

    class Actor:
        def __init__(self, request, root, **kwargs):
            roots.append(root)
            if request["parameters"].get("builtin"):
                provisioned.append(True)
                (root / "yolo11n.pt").write_bytes(payload)
            else:
                model = next(item for item in request["inputs"] if item["slot"] == "model")
                assert (root / model["path"]).read_bytes() == payload

        def infer(self, *args, **kwargs):
            return {"available": True, "outcome": "succeeded", "predictions": [{"boxes": []}]}

        def close(self):
            pass

    monkeypatch.setattr("tailcam.workloads.live.ProcessActor", Actor)
    first = live_request(storage.node_id, plan)
    assert workloads.execute_live(first)["available"] is True
    with workloads._lock:
        workloads._close_session(first.session_id)
    assert not roots[0].exists()
    assert (builtin_models_dir() / "yolo11n.pt").read_bytes() == payload
    # The suite is offline. A new child receives the validated cached bytes,
    # without a provisioning instruction or a second download.
    second = live_request(storage.node_id, plan)
    assert workloads.execute_live(second)["available"] is True
    workloads.close()
    assert provisioned == [True]
    assert not any(path.exists() for path in roots)
    assert not storage.catalog.connection.execute(
        "SELECT 1 FROM storage_artifacts LIMIT 1"
    ).fetchone()
    assert storage.enabled is False


def test_offline_yolo_training_uses_explicit_cpu_and_private_loader(tmp_path, monkeypatch):
    import sys

    from tailcam.training.runner import train_model

    calls = []
    model = SimpleNamespace(
        add_callback=lambda *args: None, train=lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda _: model))
    train_model(
        "local.pt", tmp_path, 2, 224, "cpu", tmp_path, lambda _: None, offline=True, seed=47
    )
    assert calls[0]["device"] == "cpu"
    assert calls[0]["workers"] == 0 and calls[0]["seed"] == 47
    assert calls[0]["amp"] is False and calls[0]["cache"] is False and calls[0]["plots"] is False


@pytest.mark.parametrize("task", ["motion_description", "printer_analysis", "labeling"])
def test_custom_ai_provider_is_rejected_before_source_bytes_or_child(
    store, tmp_path, monkeypatch, task
):
    config, storage, jobs = setup_storage(store, tmp_path)
    config.ai.enabled = True
    config.ai.provider = "custom-plugin"
    workloads = WorkloadService(jobs, storage, store, config)
    with monkeypatch.context() as source:
        source.setattr(storage, "put_bytes", lambda *a, **k: pytest.fail("source byte publication"))
        with pytest.raises(JobError) as exc:
            workloads.analyze(np.zeros((8, 8, 3), np.uint8), task=task, backend_hint="ollama")
        assert exc.value.code == "unsupported_provider"
    record = jobs.submit(
        JobSpec(task=task, origin_node_id=storage.node_id, parameters={"backend": "ollama"}),
        idempotency_key=task,
    )
    executor = ProcessExecutor(storage, node_id=storage.node_id, config=config)
    monkeypatch.setattr(executor.runner, "run", lambda *a, **k: pytest.fail("child started"))
    executor.execute(jobs.claim_local(), jobs)
    assert jobs.get(record.job_id).stages[0].error.code == "unsupported_provider"
    assert jobs.get(record.job_id).state == "failed"


def test_explicit_ollama_provider_overrides_worker_plugin_without_loading_it(
    store, tmp_path, monkeypatch
):
    from tailcam.jobs.models import PlacementPlan, ProviderDefinition, WorkerTarget

    config, storage, jobs = setup_storage(store, tmp_path)
    config.ai.provider = "custom-plugin"
    config.ai.prompt = "A worker-local prompt must not override an explicit provider."
    provider = jobs.placement.register_provider(
        ProviderDefinition(
            name="Explicit Ollama",
            base_url="http://127.0.0.1:1",
            model="fixture",
            tasks=["printer_analysis"],
        )
    )
    target = WorkerTarget(provider_id=provider.provider_id)
    plan = PlacementPlan(task="printer_analysis", requested_target=target, selected_target=target)
    record = jobs.submit(
        JobSpec(task="printer_analysis", origin_node_id=storage.node_id, placement_plan=plan),
        idempotency_key="explicit-provider",
    )
    executor = ProcessExecutor(storage, node_id=storage.node_id, config=config)
    calls = []

    def runtime(request, *args, **kwargs):
        calls.append(request["runtime"]["ai"])
        return {"result": {"available": True}, "outputs": []}

    monkeypatch.setattr(executor.runner, "run", runtime)
    executor.execute(jobs.claim_local(), jobs)
    assert jobs.get(record.job_id).state == "succeeded"
    assert calls[0]["provider"] == "ollama" and calls[0]["model"] == "fixture"
    assert "prompt" not in calls[0]
    assert config.ai.provider == "custom-plugin"


def test_local_durable_analysis_preserves_server_owned_prompt(store, tmp_path, monkeypatch):
    from tailcam.ai.analyzer import Analysis
    from tailcam.jobs.models import ArtifactRef

    config, storage, jobs = setup_storage(store, tmp_path)
    config.ai.enabled = True
    config.ai.prompt = "Describe activity near the workshop door using the configured JSON schema."
    image = storage.put_bytes(
        "analysis_evidence", WorkloadService.jpeg(np.zeros((8, 8, 3), np.uint8))
    )
    record = jobs.submit(
        JobSpec(
            task="motion_description",
            origin_node_id=storage.node_id,
            parameters={"backend": "ollama"},
            input_artifacts=[ArtifactRef.from_artifact(image, "image")],
        ),
        idempotency_key="saved-prompt",
    )
    received = []

    def analyzer(ai):
        received.append(ai.prompt)
        return SimpleNamespace(
            analyze=lambda _: Analysis("nothing", "No activity", 1.0), close=lambda: None
        )

    monkeypatch.setattr("tailcam.ai.analyzer.OllamaAnalyzer", analyzer)
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("network client created"))
    executor = ProcessExecutor(storage, node_id=storage.node_id, config=config)
    monkeypatch.setattr(
        executor.runner,
        "run",
        lambda request, root, **kwargs: handlers.run_handler(request, root, lambda _: None),
    )
    executor.execute(jobs.claim_local(), jobs)
    assert jobs.get(record.job_id).state == "succeeded"
    assert received == [config.ai.prompt]


def test_child_custom_provider_guard_precedes_analyzer_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tailcam.ai.analyzer.OllamaAnalyzer", lambda *a, **k: pytest.fail("Ollama fallback")
    )
    with pytest.raises(ExecutionError) as exc:
        handlers._inference(
            "motion_description",
            {"backend": "ollama"},
            [],
            tmp_path,
            {"ai": {"provider": "custom-plugin"}},
            1024,
        )
    assert exc.value.code == "unsupported_provider"
