"""Active learning: annotation conversion, uncertainty routing, the Label
Studio service (against a fake SDK client), the capture→route→review loop, and
sync — exercised directly and through the REST API."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tailcam.activelearning.annotations import (
    FrameAnnotation,
    from_label_studio_result,
    label_config_xml,
    to_florence_od_string,
    to_label_studio_predictions,
    to_qwen_json,
    to_yolo_lines,
)
from tailcam.activelearning.labelstudio import LabelStudioError, LabelStudioService
from tailcam.activelearning.service import route_frame
from tailcam.ai.analyzer import Detection
from tailcam.persistence.models import ReviewItemRecord


def _wait(predicate, timeout=10.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# -- routing -----------------------------------------------------------------


def test_route_frame_decisions():
    hi = [Detection(label="person", confidence=0.9, cx=0.5, cy=0.5, w=0.2, h=0.2)]
    lo = [Detection(label="person", confidence=0.3, cx=0.5, cy=0.5, w=0.2, h=0.2)]
    mixed = hi + lo
    assert route_frame(hi, 0.6) == "auto"
    assert route_frame(lo, 0.6) == "review"
    assert route_frame(mixed, 0.6) == "review"  # any uncertain box -> human
    assert route_frame([], 0.6) == "skip"
    assert route_frame([], 0.6, review_empty=True) == "review"
    assert route_frame(None, 0.6) == "skip"  # inference failure must not flood LS
    assert route_frame(None, 0.6, review_empty=True) == "skip"


# -- converters ----------------------------------------------------------------


def test_label_config_supports_multiple_regions():
    xml = label_config_xml(["person", "package"])
    assert "RectangleLabels" in xml  # multiple boxes per image by design
    assert '<Label value="person"/>' in xml
    assert '<Label value="package"/>' in xml


def test_label_studio_roundtrip():
    boxes = [
        FrameAnnotation(label="person", cx=0.5, cy=0.5, w=0.2, h=0.4, confidence=0.4),
        FrameAnnotation(label="dog", cx=0.25, cy=0.75, w=0.1, h=0.1, confidence=0.3),
    ]
    pred = to_label_studio_predictions(boxes, "builtin")
    assert pred["model_version"] == "builtin"
    assert pred["score"] == 0.3  # lowest confidence drives review priority
    assert len(pred["result"]) == 2
    # LS rectangles are percent top-left/size
    r0 = pred["result"][0]["value"]
    assert r0["x"] == pytest.approx(40.0)
    assert r0["y"] == pytest.approx(30.0)
    assert r0["width"] == pytest.approx(20.0)
    assert r0["height"] == pytest.approx(40.0)

    # A human edits/completes it in LS -> convert back to canonical boxes.
    back = from_label_studio_result(pred["result"])
    assert len(back) == 2
    assert back[0].label == "person"
    assert back[0].cx == pytest.approx(0.5)
    assert back[0].cy == pytest.approx(0.5)
    assert back[0].source == "human"


def test_from_label_studio_skips_non_rectangles():
    result = [
        {"type": "choices", "value": {"choices": ["ok"]}},
        {"type": "rectanglelabels", "value": {"x": 10, "y": 10, "width": 0, "height": 5,
                                              "rectanglelabels": ["x"]}},  # zero area
        {"type": "rectanglelabels", "value": {"x": 10, "y": 20, "width": 30, "height": 40,
                                              "rectanglelabels": ["cat"]}},
    ]
    boxes = from_label_studio_result(result)
    assert len(boxes) == 1 and boxes[0].label == "cat"


def test_training_format_targets():
    boxes = [FrameAnnotation(label="person", cx=0.5, cy=0.5, w=0.5, h=0.5)]
    assert to_yolo_lines(boxes, {"person": 0}) == ["0 0.500000 0.500000 0.500000 0.500000"]
    assert to_yolo_lines(boxes, {"other": 0}) == []  # unknown class dropped
    flo = to_florence_od_string(boxes)
    assert flo.startswith("person<loc_249><loc_249>")
    qwen = to_qwen_json(boxes, 640, 480)
    assert '"bbox_2d": [160, 120, 480, 360]' in qwen and '"label": "person"' in qwen


# -- Label Studio service (fake SDK client) -------------------------------------


class _FakeProjects:
    def __init__(self, state):
        self.state = state

    def list(self, page_size=None):
        if self.state.get("down"):
            raise ConnectionError("connection refused")
        return list(self.state["projects"])

    def get(self, id):  # noqa: A002 - mirrors the SDK signature
        for p in self.state["projects"]:
            if p.id == id:
                return p
        raise LookupError("404 not found")

    def create(self, title, description="", label_config=""):
        project = SimpleNamespace(id=len(self.state["projects"]) + 1, title=title,
                                  task_number=0, label_config=label_config)
        self.state["projects"].append(project)
        return project

    def import_tasks(self, id, request, return_task_ids=True):  # noqa: A002
        self.state["imported"].extend(request)
        first = 100 + len(self.state["imported"])
        return {"task_ids": [first]}


class _FakeTasks:
    def __init__(self, state):
        self.state = state

    def list(self, project, fields="all"):
        return list(self.state["tasks"])


class _FakeLabelStudio:
    def __init__(self, state):
        self.projects = _FakeProjects(state)
        self.tasks = _FakeTasks(state)


@pytest.fixture
def ls_state():
    return {"projects": [], "tasks": [], "imported": [], "down": False}


@pytest.fixture
def ls_service(context, ls_state):
    cfg = context.config.active_learning
    cfg.label_studio_url = "http://localhost:8080"
    cfg.label_studio_token = "test-token"
    service = LabelStudioService(cfg, client_factory=lambda url, token: _FakeLabelStudio(ls_state))
    context.active_learning.label_studio = service
    return service


def test_labelstudio_status_reports_missing_token(context):
    service = LabelStudioService(context.config.active_learning,
                                 client_factory=lambda u, t: None)
    status = service.status()
    assert status.configured is False and status.connected is False
    assert "token" in status.error


def test_labelstudio_status_connection_refused(ls_service, ls_state):
    ls_state["down"] = True
    status = ls_service.status()
    assert status.connected is False
    assert "label-studio start" in status.error  # actionable, token-free
    assert "test-token" not in status.error


def test_ensure_project_creates_then_reuses(ls_service, context, ls_state):
    pid = ls_service.ensure_project(["person", "package"])
    assert pid == 1
    assert context.config.active_learning.project_id == 1
    assert "RectangleLabels" in ls_state["projects"][0].label_config
    assert ls_service.ensure_project(["person"]) == 1  # validated, not re-created


def test_ensure_project_missing_id_is_friendly(ls_service, context):
    context.config.active_learning.project_id = 42
    with pytest.raises(LabelStudioError, match="project #42 not found"):
        ls_service.ensure_project(["person"])


def test_submit_and_pull_completed(ls_service, ls_state, tmp_path):
    from tailcam.activelearning.annotations import AnnotatedFrame

    img = tmp_path / "frame.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    frame = AnnotatedFrame(
        image_path=str(img),
        annotations=[FrameAnnotation(label="person", cx=0.5, cy=0.5, w=0.2, h=0.2,
                                     confidence=0.4)],
        camera_id="cam0",
        labeling_model="builtin",
    )
    task_id = ls_service.submit_frame(1, frame, "builtin")
    assert task_id == 101
    imported = ls_state["imported"][0]
    assert imported["data"]["image"].startswith("data:image/jpeg;base64,")
    assert imported["data"]["meta"]["tailcam_image_path"] == str(img)
    assert imported["predictions"][0]["result"], "model boxes ship as pre-annotations"

    # Human completes it in Label Studio.
    ls_state["tasks"] = [
        {
            "id": 101,
            "data": {"image": "...", "meta": {"tailcam_image_path": str(img)}},
            "annotations": [
                {
                    "was_cancelled": False,
                    "result": [
                        {"type": "rectanglelabels",
                         "value": {"x": 25, "y": 25, "width": 50, "height": 50,
                                   "rectanglelabels": ["person"]}},
                        {"type": "rectanglelabels",
                         "value": {"x": 0, "y": 0, "width": 10, "height": 10,
                                   "rectanglelabels": ["package"]}},
                    ],
                }
            ],
        }
    ]
    completed = ls_service.pull_completed(1)
    assert len(completed) == 1
    assert completed[0].image_path == str(img)
    assert [a.label for a in completed[0].annotations] == ["person", "package"]


# -- the loop -------------------------------------------------------------------


class _StubBackend:
    """A deterministic labeling model for loop tests."""

    def __init__(self, detections):
        self.detections = detections

    def info(self):
        from tailcam.activelearning.backends import BackendInfo

        return BackendInfo(id="stub", name="Stub", kind="detector", available=True,
                           detail="ready")

    def predict(self, image):
        return list(self.detections)


def _start_loop(context, monkeypatch, detections, ls_service):
    from tailcam.activelearning import service as service_mod

    cfg = context.config.active_learning
    cfg.interval_seconds = 1.0
    cfg.confidence_threshold = 0.6
    monkeypatch.setattr(
        service_mod, "build_labeling_backend", lambda *a, **k: _StubBackend(detections)
    )
    context.active_learning.start()


def test_loop_auto_labels_confident_frames(context, monkeypatch, ls_service):
    _start_loop(
        context, monkeypatch,
        [Detection(label="person", confidence=0.95, cx=0.5, cy=0.5, w=0.4, h=0.4)],
        ls_service,
    )
    try:
        assert _wait(lambda: context.active_learning.stats().auto_labeled >= 1)
    finally:
        context.active_learning.stop()
    stats = context.active_learning.stats()
    ds_id = stats.dataset_id
    samples = context.store.list_samples(ds_id)
    auto = [s for s in samples if s.source == "active-auto"]
    assert auto, "confident frames become machine-labeled samples"
    assert auto[0].label == "person"
    assert auto[0].confidence == pytest.approx(0.95)
    boxes = context.store.list_annotations(auto[0].id)
    assert boxes and boxes[0].label == "person"
    assert context.store.list_review_items(status="pending") == []


def test_loop_sends_uncertain_frames_for_review(context, monkeypatch, ls_service, ls_state):
    ls_service.ensure_project(["person"])
    _start_loop(
        context, monkeypatch,
        [Detection(label="person", confidence=0.30, cx=0.5, cy=0.5, w=0.4, h=0.4)],
        ls_service,
    )
    try:
        assert _wait(lambda: context.active_learning.stats().sent_for_review >= 1)
    finally:
        context.active_learning.stop()
    pending = context.store.list_review_items(status="pending")
    assert pending and pending[0].labeling_model == "builtin"
    assert pending[0].confidence == pytest.approx(0.30)
    assert ls_state["imported"], "the frame was imported into Label Studio"
    sample = context.store.get_sample(pending[0].sample_id)
    assert sample is not None and sample.source == "active-review"
    assert sample.label is None  # awaiting the human


def test_review_cap_stops_submissions(context, monkeypatch, ls_service):
    context.config.active_learning.max_review_per_session = 1
    ls_service.ensure_project(["person"])
    _start_loop(
        context, monkeypatch,
        [Detection(label="person", confidence=0.2, cx=0.5, cy=0.5, w=0.4, h=0.4)],
        ls_service,
    )
    try:
        assert _wait(
            lambda: context.active_learning.stats().sent_for_review >= 1
            and context.active_learning.stats().skipped >= 1
        )
    finally:
        context.active_learning.stop()
    assert context.active_learning.stats().sent_for_review == 1
    assert "review cap" in context.active_learning.stats().last_error


def test_sync_writes_human_annotations(context, ls_service, ls_state, tmp_path):
    from tailcam.persistence.models import DatasetSampleRecord

    ds = context.training.create_dataset("AL", task="detection")
    img = tmp_path / "review.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    sid = context.store.add_sample(
        DatasetSampleRecord(
            id=None, dataset_id=ds.id, path=str(img), thumb=None, label=None,
            source="active-review", camera_id="cam0", host="vm", created_ts=time.time(),
        )
    )
    context.store.add_review_item(
        ReviewItemRecord(
            id=None, sample_id=sid, dataset_id=ds.id, ls_project_id=1, ls_task_id=101,
            status="pending", labeling_model="builtin", confidence=0.3,
            created_ts=time.time(),
        )
    )
    ls_state["tasks"] = [
        {
            "id": 101,
            "data": {"image": "...", "meta": {"tailcam_image_path": str(img)}},
            "annotations": [
                {"was_cancelled": False,
                 "result": [{"type": "rectanglelabels",
                             "value": {"x": 25, "y": 25, "width": 50, "height": 50,
                                       "rectanglelabels": ["package"]}}]}
            ],
        }
    ]
    result = context.active_learning.sync()
    assert result["completed"] == 1
    assert result["dataset_version"] == 2  # bumped by the sync
    boxes = context.store.list_annotations(sid)
    assert boxes and boxes[0].label == "package"
    assert context.store.get_sample(sid).label == "package"
    assert context.store.list_review_items(status="completed")
    assert context.store.list_review_items(status="pending") == []
    # Second sync is a no-op, not a duplicate.
    assert context.active_learning.sync()["completed"] == 0


# -- REST API --------------------------------------------------------------------


def test_api_info_and_settings_roundtrip(client):
    info = client.get("/api/active-learning").json()
    assert info["running"] is False
    assert info["labeling_model"] == "builtin"
    assert info["token_set"] is False
    assert info["platform"]["os"] in ("linux", "macos", "windows")

    updated = client.post(
        "/api/active-learning/settings",
        json={"confidence_threshold": 0.8, "label_studio_token": "super-secret",
              "source": "cameras", "interval_seconds": 5},
    ).json()
    assert updated["confidence_threshold"] == 0.8
    assert updated["token_set"] is True
    assert "super-secret" not in str(updated)  # token is write-only

    assert client.post(
        "/api/active-learning/settings", json={"finetune_model": "nope"}
    ).status_code == 400


def test_api_url_change_clears_project(client):
    client.post("/api/active-learning/settings", json={"project_id": 7})
    updated = client.post(
        "/api/active-learning/settings", json={"label_studio_url": "http://other:8080"}
    ).json()
    assert updated["project_id"] == 0  # different server, different projects


def test_api_backends_report_availability(client):
    backends = client.get("/api/active-learning/backends").json()
    ids = {b["id"] for b in backends}
    assert {"builtin", "ollama", "florence2", "qwen2.5-vl"} <= ids
    florence = next(b for b in backends if b["id"] == "florence2")
    # Torch isn't installed in CI — the backend must say what to install.
    assert florence["available"] is False
    assert "tailcam[florence2]" in florence["detail"]

    targets = client.get("/api/active-learning/finetune-backends").json()
    assert {t["id"] for t in targets} == {"yolo", "florence2", "qwen2.5-vl"}
    for t in targets:
        if not t["available"]:
            assert t["detail"], "unavailable targets explain why"


def test_api_start_without_label_studio_fails_cleanly(client):
    r = client.post("/api/active-learning/start")
    assert r.status_code == 503
    assert "token" in r.json()["detail"]
    assert client.get("/api/active-learning").json()["running"] is False


def test_api_start_rejects_unknown_model(client):
    client.post("/api/active-learning/settings", json={"labeling_model": "bogus"})
    r = client.post("/api/active-learning/start")
    assert r.status_code == 400
    assert "unknown labeling model" in r.json()["detail"]


def test_api_train_requires_dataset(client):
    r = client.post("/api/active-learning/train", json={})
    assert r.status_code == 400
    assert "dataset" in r.json()["detail"]


def test_api_train_vlm_unavailable_explains(client, context):
    ds = context.training.create_dataset("AL", task="detection")
    client.post("/api/active-learning/settings",
                json={"dataset_id": ds.id, "finetune_model": "qwen2.5-vl"})
    r = client.post("/api/active-learning/train", json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "unavailable" in detail and ("CUDA" in detail or "unsloth" in detail.lower())


def test_api_sync_route(client, ls_service):
    r = client.post("/api/active-learning/sync")
    assert r.status_code == 200
    assert r.json() == {"completed": 0, "pending": 0, "dataset_version": 0}


def test_stats_survive_stop(context, monkeypatch, ls_service):
    _start_loop(
        context, monkeypatch,
        [Detection(label="person", confidence=0.95, cx=0.5, cy=0.5, w=0.4, h=0.4)],
        ls_service,
    )
    try:
        assert _wait(lambda: context.active_learning.stats().frames_processed >= 1)
    finally:
        context.active_learning.stop()
    stats = context.active_learning.stats()
    assert stats.running is False
    assert stats.frames_processed >= 1  # last session's counters remain visible
