"""Disabled workload roles stay disabled beneath transports and status reads."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tailcam.config import AppConfig
from tailcam.node import NodeConfigError, RoleDisabledError
from tailcam.timelapse.analyzer import PrinterAnalysis, TimelapseAnalysisQueue
from tailcam.web.context import AppContext


@pytest.fixture
def node_context(store, monkeypatch):
    contexts = []
    monkeypatch.setattr("tailcam.web.context.resolve_local_host", lambda _: "isolated-node")

    def make(roles):
        config = AppConfig()
        config.node.roles = list(roles)
        config.tailscale.auto_serve = False
        config.peers.auto_discover = False
        config.ai.base_url = "http://127.0.0.1:1"
        context = AppContext(config, store)
        monkeypatch.setattr(context, "_start_notify_monitor", lambda: None)
        contexts.append(context)
        return context

    yield make
    for context in contexts:
        context.shutdown()


def _forbidden(*args, **kwargs):
    pytest.fail("disabled workload was initialized")


def test_hub_startup_and_status_do_not_initialize_workloads(node_context, monkeypatch):
    from tailcam.camera import enumerate as cameras
    from tailcam.plugins.registry import PluginRegistry
    from tailcam.training.inference import LocalClassifier, LocalDetector

    monkeypatch.setattr(cameras, "discover", _forbidden)
    monkeypatch.setattr(PluginRegistry, "analyzer_provider", _forbidden)
    monkeypatch.setattr(LocalClassifier, "load", _forbidden)
    monkeypatch.setattr(LocalDetector, "load", _forbidden)
    monkeypatch.setattr("tailcam.web.context.use_synthetic", lambda: False)
    ctx = node_context([])
    # Saved workload switches and a stale selected model must not override roles.
    ctx.config.training.collect_enabled = True
    ctx.config.training.active_model_id = 123
    ctx.config.ai.enabled = True
    ctx.config.detection.enabled = True
    ctx.config.detection.node = "http://127.0.0.1:1"
    ctx.config.homekit.enabled = True
    ctx.config.homeassistant.enabled = True
    monkeypatch.setattr(ctx.detector, "ensure_ready", _forbidden)
    monkeypatch.setattr(ctx.training, "startup", _forbidden)
    monkeypatch.setattr(ctx.homekit, "start", _forbidden)
    monkeypatch.setattr("tailcam.web.context.MqttPublisher", _forbidden)
    ctx.startup()
    ctx.apply_homeassistant_config()
    ctx._rediscover_if_offline()
    image = np.zeros((2, 2, 3), dtype=np.uint8)

    assert ctx.manager.list() == []
    assert ctx.manager.discover() == []
    assert ctx.manager.get_buffer("/dev/video0") is None
    assert not ctx.manager.restart("/dev/video0")
    assert ctx.analyzer.health() == (False, None)
    assert ctx.analyzer.installed_models() == (False, [])
    assert ctx.analyzer.analyze(image) is None
    assert ctx.inference.describe()["mode"] == "off"
    assert not ctx.inference.enabled
    assert not ctx.inference.detection_active
    assert ctx.inference.detect(image) is None
    assert ctx.inference.analyze(image) is None
    assert ctx.detector.status().status == "off"
    assert ctx.printer_analyzer.analyze_path(Path("not-read.jpg")) is None
    assert ctx.timelapse_analysis._thread is None
    assert not ctx.training.is_collecting()
    assert not ctx.active_learning.is_running()
    assert ctx.ha_mqtt is None


def test_role_changes_need_restart_even_for_lazy_camera_access(node_context, monkeypatch):
    monkeypatch.setattr("tailcam.camera.enumerate.discover", _forbidden)
    ctx = node_context([])
    identity = ctx.node_id
    ctx.config.node.roles = ["capture", "storage", "analysis", "training"]
    assert ctx.active_roles == frozenset()
    assert not ctx.has_role("capture")
    assert ctx.manager.discover() == []
    assert ctx.manager.get_buffer("synthetic:0") is None
    assert not ctx.detector.enabled
    assert ctx.node_id == identity
    with pytest.raises(RoleDisabledError, match="capture"):
        ctx.enable_motion("synthetic:0")


def test_context_rejects_invalid_programmatic_role_changes(store):
    config = AppConfig()
    config.node.roles = ["capture", "unknown"]
    with pytest.raises(NodeConfigError):
        AppContext(config, store)


def test_hub_rejects_service_work_before_writing_or_starting_threads(node_context):
    ctx = node_context([])
    operations = [
        ("storage", lambda: ctx.snapshots.capture("synthetic:0")),
        ("storage", lambda: ctx.recorder.start("synthetic:0")),
        ("storage", lambda: ctx.timelapse.start("synthetic:0")),
        ("analysis", lambda: ctx.pulls.start("test-model")),
        ("analysis", lambda: ctx.analyzer.load("test-model")),
        ("analysis", lambda: ctx.analyzer.pull("test-model")),
        ("analysis", lambda: ctx.timelapse_analysis.submit(1, 1, Path("unused"))),
        ("training", ctx.training.startup),
        ("training", ctx.training.start_collection),
        ("training", lambda: ctx.training.create_dataset("unused")),
        ("training", lambda: ctx.training.delete_dataset(1)),
        ("training", lambda: ctx.training.delete_sample(1)),
        ("training", lambda: ctx.training.set_annotations(1, [])),
        ("training", lambda: ctx.training.import_from_events(1)),
        ("analysis", lambda: ctx.training.register_byo("unused", "missing.pt")),
        ("analysis", lambda: ctx.training.activate_model(1)),
        ("training", lambda: ctx.training.delete_model(1)),
        ("training", lambda: ctx.training.train(1)),
        ("training", ctx.active_learning.start),
        ("training", ctx.active_learning.sync),
        ("training", ctx.active_learning.train),
    ]
    for role, operation in operations:
        with pytest.raises(RoleDisabledError) as exc:
            operation()
        assert exc.value.role == role
    assert ctx.store.list_datasets() == []
    assert ctx.store.list_models() == []
    assert ctx.store.list_runs() == []
    assert ctx.timelapse_analysis._thread is None
    assert not ctx.pulls.status().active


def test_training_role_does_not_enable_active_learning_inference(node_context, monkeypatch):
    ctx = node_context(["training"])
    monkeypatch.setattr("tailcam.activelearning.service.build_labeling_backend", _forbidden)
    with pytest.raises(RoleDisabledError) as exc:
        ctx.active_learning.start()
    assert exc.value.role == "analysis"
    assert not ctx.active_learning.is_running()


def test_analysis_worker_can_select_models_without_training_role(node_context, tmp_path):
    ctx = node_context(["analysis"])
    weights = tmp_path / "external.pt"
    weights.write_bytes(b"test model registry entry; weights are not loaded")
    registered = ctx.training.register_byo("External", str(weights))
    assert registered is not None
    ctx.training.activate_model(registered.id)
    assert ctx.config.training.active_model_id == registered.id
    ctx.training.activate_model(None)
    assert ctx.config.training.active_model_id == 0
    with pytest.raises(RoleDisabledError) as exc:
        ctx.training.start_collection()
    assert exc.value.role == "training"


def test_capture_node_can_use_explicit_remote_detection_without_loading_models(
    node_context, monkeypatch
):
    ctx = node_context(["capture"])
    ctx.config.detection.node = "peer"
    remote = SimpleNamespace(
        available=True,
        last_error="",
        model_name=lambda: "peer model",
        analyze=lambda _: "remote analysis",
        detect=lambda _: [],
    )
    monkeypatch.setattr(ctx.inference, "_remote", lambda: remote)
    monkeypatch.setattr("tailcam.training.inference.LocalDetector.load", _forbidden)
    ctx.config.training.active_model_id = 1
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    assert ctx.inference.enabled
    assert ctx.inference.detection_active
    assert ctx.inference.describe()["mode"] == "remote"
    assert ctx.inference.describe()["error"] == ""
    assert ctx.inference.analyze(image) == "remote analysis"
    assert ctx.inference.detect(image) == []
    assert not ctx.detector.enabled


def test_timelapse_analysis_queue_is_lazy_and_reuses_one_worker():
    written = threading.Event()
    records = []

    def save(record):
        records.append(record)
        written.set()

    queue = TimelapseAnalysisQueue(
        SimpleNamespace(add_timelapse_analysis_event=save),
        SimpleNamespace(analyze_path=lambda _: PrinterAnalysis("healthy", 1, "ok")),
    )
    assert queue._thread is None
    try:
        queue.submit(1, 1, Path("evidence"))
        thread = queue._thread
        assert thread is not None
        assert written.wait(2)
        queue.submit(2, 1, Path("evidence"))
        assert queue._thread is thread
    finally:
        queue.shutdown()
    assert not thread.is_alive()
    assert records[0].state == "healthy"


def test_timelapse_analysis_queue_can_shutdown_before_first_use():
    queue = TimelapseAnalysisQueue(SimpleNamespace(), SimpleNamespace())
    queue.shutdown()
    queue.shutdown()
    queue.submit(1, 1, Path("unused"))
    assert queue._thread is None
