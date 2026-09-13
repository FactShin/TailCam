"""Frozen live sessions and durable source-side analysis dispatch.

Live images are ephemeral. Each session has one running frame and one newest
pending frame; worker retries never reroute through another source router.
"""

from __future__ import annotations

import base64
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tailcam.jobs.models import ArtifactRef, JobError, JobSpec, PlacementPlan, ResultManifest
from tailcam.workloads.executor import validate_budget
from tailcam.workloads.process import ExecutionError, ProcessActor


class LiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    target_node_id: str
    coordinator_node_id: str
    plan: PlacementPlan
    jpeg: str = Field(max_length=16 * 1024**2)
    deadline_at: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("target_node_id", "coordinator_node_id")
    @classmethod
    def uuid_value(cls, value: str) -> str:
        return str(UUID(value))


@dataclass
class _Frame:
    request: LiveRequest
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: Exception | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class _Session:
    plan: PlacementPlan
    lease: Any
    actor: ProcessActor
    pending: _Frame | None = None
    running: bool = False
    last_used: float = field(default_factory=time.monotonic)
    model_name: str = ""
    coordinator_node_id: str = ""
    provisioned_builtin: str = ""


class WorkloadService:
    def __init__(
        self, jobs: Any, storage: Any, store: Any, config: Any, *, resolve_legacy_node=None
    ) -> None:
        self.jobs, self.storage, self.store, self.config = jobs, storage, store, config
        self.node_id = jobs.node_id
        self.resolve_legacy_node = resolve_legacy_node or (lambda _: None)
        self._sessions: dict[str, _Session] = {}
        self._source_plans: dict[str, tuple[str, PlacementPlan]] = {}
        self._source_used: dict[str, float] = {}
        self._status: dict[str, dict] = {}
        self._observation_lock = threading.Lock()
        self._local_live_observation: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._reaper = threading.Thread(
            target=self._reap, name="workload-live-cleanup", daemon=True
        )
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._reaper.start()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            for identity in list(self._sessions):
                self._close_session(identity)
        if self._started:
            self._reaper.join(timeout=2)

    def _reap(self) -> None:
        while not self._closed.wait(1):
            with self._lock:
                for identity, session in list(self._sessions.items()):
                    if not session.running and time.monotonic() - session.last_used > 30:
                        self._close_session(identity)
                for camera, used in list(self._source_used.items()):
                    if time.monotonic() - used > 30:
                        self._source_plans.pop(camera, None)
                        self._source_used.pop(camera, None)

    def _close_session(self, identity: str) -> None:
        session = self._sessions.pop(identity, None)
        if session is None:
            return
        session.actor.close()
        session.lease.release()
        self.jobs.release_live(identity)
        if session.pending is not None:
            session.pending.error = JobError("session_closed", "Live session closed.")
            session.pending.done.set()

    @staticmethod
    def jpeg(image) -> bytes:
        import cv2

        height, width = image.shape[:2]
        if width > 1280:
            image = cv2.resize(image, (1280, max(1, int(height * 1280 / width))))
        ok, result = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok or result.nbytes > 12 * 1024**2:
            raise JobError("invalid_image", "Image could not be encoded within its byte limit.")
        return result.tobytes()

    def detect(self, image, *, camera_id: str = "", local_only: bool = False) -> dict:
        if local_only:
            camera_id = "local:" + camera_id
        with self._lock:
            self._source_used[camera_id] = time.monotonic()
            if camera_id not in self._source_plans:
                if len(self._source_plans) >= 64:
                    raise JobError("live_queue_full", "Too many camera sessions are open.")
                self._source_plans[camera_id] = (
                    str(uuid4()),
                    self._plan("live_detection", local_only=local_only),
                )
            identity, plan = self._source_plans[camera_id]
        request = LiveRequest(
            session_id=identity,
            request_id=str(uuid4()),
            target_node_id=plan.selected_target.node_id or self.node_id,
            plan=plan,
            coordinator_node_id=self.node_id,
            jpeg=base64.b64encode(self.jpeg(image)).decode("ascii"),
            deadline_at=time.time() + min(30, plan.budget.wall_seconds),
        )
        started = time.monotonic()
        if request.target_node_id == self.node_id:
            result = self.execute_live(request)
        else:
            if self.jobs.transport is None:
                raise JobError("worker_unavailable", "Remote live transport is unavailable.")
            result = self.jobs.transport.request(
                request.target_node_id,
                "POST",
                "/api/v1/workloads/live/execute",
                json=request.model_dump(mode="json"),
                timeout=min(30, plan.budget.wall_seconds),
            )
        manifest = ResultManifest.model_validate({"result": result})
        self._validate_live_result(manifest.result, request)
        with self._lock:
            self._status[camera_id] = {
                "worker_node_id": result.get("worker_node_id", request.target_node_id),
                "model_name": result.get("model", ""),
                "execution_ms": result.get("execution_ms"),
                "queue_ms": result.get("queue_ms"),
                "round_trip_ms": (time.monotonic() - started) * 1000,
                "session_id": request.session_id,
            }
        return manifest.result

    @staticmethod
    def _validate_live_result(result: dict, request: LiveRequest) -> None:
        valid = (
            result.get("available") is True
            and result.get("outcome") == "succeeded"
            and result.get("worker_node_id") == request.target_node_id
            and result.get("session_id") == request.session_id
            and isinstance(result.get("model"), str)
            and len(result["model"]) <= 256
        )
        try:
            predictions = result["predictions"]
            if not isinstance(predictions, list) or len(predictions) != 1:
                raise ValueError
            boxes = predictions[0]["boxes"]
            if not isinstance(boxes, list) or len(boxes) > 1000:
                raise ValueError
            for box in boxes:
                if not isinstance(box["label"], str) or not 1 <= len(box["label"]) <= 256:
                    raise ValueError
                for key in ("confidence", "cx", "cy", "w", "h"):
                    value = box[key]
                    if (
                        type(value) not in {int, float}
                        or not math.isfinite(value)
                        or not 0 <= value <= 1
                    ):
                        raise ValueError
            for key in ("execution_ms", "queue_ms"):
                value = result[key]
                if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                    raise ValueError
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise JobError(
                "invalid_result", "Live worker returned an invalid or mismatched result."
            )

    def _plan(self, task, *, local_only: bool = False):
        from tailcam.jobs.models import WorkerTarget

        target = WorkerTarget(node_id=self.node_id) if local_only else None
        policy = self.jobs.get_policy()
        legacy = self.config.detection.node.strip()
        if (
            not local_only
            and task not in policy.routes
            and legacy
            and (
                task == "live_detection"
                or (task == "motion_description" and not self.config.ai.enabled)
            )
        ):
            identity = self.resolve_legacy_node(legacy)
            if identity is None:
                raise JobError(
                    "worker_unavailable", "Saved detection node is unavailable or unapproved."
                )
            target = WorkerTarget(node_id=identity)
        route = policy.routes.get(task)
        requested = target or (route.target if route else None)
        local_request = requested is None or requested.node_id in {None, self.node_id}
        if local_request and (
            local_only or route is None or (route.mode == "manual" and not route.fallback_targets)
        ):
            self.jobs.role_check("training" if task == "training" else "analysis")
        return self.jobs.placement.plan(task, target=target, policy_snapshot=policy)

    def status(self, camera_id: str = "") -> dict:
        with self._lock:
            return dict(self._status.get(camera_id, {}))

    def local_live_observation(self) -> dict[str, Any]:
        """Return only verified local execution evidence without touching a worker.

        This uses a separate lock because placement reads worker inventory while
        holding the session lock. Remote source-side results never write this state.
        """
        with self._observation_lock:
            return dict(self._local_live_observation)

    def _open_session(self, request: LiveRequest) -> _Session:
        budget = request.plan.budget
        validate_budget(budget)
        self.jobs.reserve_live(request.session_id, budget)
        lease = None
        try:
            lease = self.storage.workspace_for_task(
                "live_detection", max_bytes=budget.workspace_bytes
            )
            parameters, inputs = self._model_inputs(request.plan, lease)
            actual_model = parameters.pop("_model_name", request.plan.selected_target.model)
            inputs.append({"slot": "image", "path": "frame.jpg"})
            actor = ProcessActor(
                {
                    "task": "live_detection",
                    "parameters": parameters,
                    "inputs": inputs,
                    "runtime": {},
                    "workspace_bytes": budget.workspace_bytes,
                    "output_bytes": budget.output_bytes,
                    "output_slots": [],
                    "gpu_slots": budget.gpu_slots,
                    "cpu_threads": budget.cpu_threads,
                },
                lease.path,
                deadline=time.time() + budget.wall_seconds,
            )
            session = _Session(request.plan.model_copy(deep=True), lease, actor)
            session.model_name = actual_model
            session.coordinator_node_id = request.coordinator_node_id
            session.provisioned_builtin = parameters.get("builtin", "")
            return session
        except Exception:
            with self._observation_lock:
                self._local_live_observation = {}
            if lease is not None:
                lease.release()
            self.jobs.release_live(request.session_id)
            raise

    def _model_inputs(self, plan: PlacementPlan, lease) -> tuple[dict, list]:
        from tailcam.workloads.models import local_model_inputs

        return local_model_inputs(
            self.storage,
            self.store,
            self.config,
            plan.selected_target.model,
            lease,
            "live_detection",
        )

    def execute_live(self, request: LiveRequest | dict) -> dict:
        request = LiveRequest.model_validate(request)
        if self._closed.is_set():
            raise JobError("worker_unavailable", "The live worker is shutting down.")
        if (
            request.target_node_id != self.node_id
            or request.plan.selected_target.node_id != self.node_id
        ):
            raise JobError("wrong_worker", "Live assignment is not for this worker.", 403)
        if request.plan.task != "live_detection" or request.plan.selected_target.provider_id:
            raise JobError("unsupported_task", "Live worker requires a local object detector.")
        if request.deadline_at <= time.time() or request.deadline_at > time.time() + 60:
            raise JobError("invalid_deadline", "Invalid live inference deadline.", 422)
        try:
            jpeg = base64.b64decode(request.jpeg, validate=True)
        except ValueError:
            raise JobError("invalid_image", "Image is not valid base64.", 422) from None
        if not jpeg or len(jpeg) > 12 * 1024**2:
            raise JobError("invalid_image", "Image exceeds its byte bound.", 413)
        from tailcam.streaming.image_validation import raster_dimensions

        raster_dimensions(jpeg)
        frame = _Frame(request)
        with self._lock:
            session = self._sessions.get(request.session_id)
            if session is None:
                session = self._open_session(request)
                self._sessions[request.session_id] = session
            if (
                session.plan != request.plan
                or session.coordinator_node_id != request.coordinator_node_id
            ):
                raise JobError("session_conflict", "Live session placement is immutable.")
            if session.pending is not None:
                session.pending.error = JobError(
                    "superseded", "A newer camera frame replaced this one."
                )
                session.pending.done.set()
            session.pending = frame
            session.last_used = time.monotonic()
            if not session.running:
                session.running = True
                threading.Thread(
                    target=self._frames,
                    args=(request.session_id,),
                    daemon=True,
                    name="workload-live-frames",
                ).start()
        if not frame.done.wait(max(0, request.deadline_at - time.time()) + 0.5):
            raise JobError("deadline_exceeded", "Live inference exceeded its deadline.")
        if frame.error:
            raise frame.error
        return frame.result or {"available": False, "outcome": "unavailable"}

    def _frames(self, identity: str) -> None:
        while not self._closed.is_set():
            with self._lock:
                session = self._sessions.get(identity)
                if session is None:
                    return
                frame, session.pending = session.pending, None
                if frame is None:
                    session.running = False
                    return
            try:
                started = time.monotonic()
                frame.result = session.actor.infer(
                    frame.request.request_id,
                    base64.b64decode(frame.request.jpeg, validate=True),
                    deadline=frame.request.deadline_at,
                    check_workspace=session.lease.check,
                )
                if session.provisioned_builtin:
                    from tailcam.workloads.models import persist_builtin

                    persist_builtin(session.lease.path, session.provisioned_builtin)
                    session.provisioned_builtin = ""
                frame.result.update(
                    worker_node_id=self.node_id,
                    model=getattr(session, "model_name", session.plan.selected_target.model),
                    session_id=identity,
                    execution_ms=(time.monotonic() - started) * 1000,
                    queue_ms=(started - frame.enqueued_at) * 1000,
                )
                self._validate_live_result(frame.result, frame.request)
                with self._observation_lock:
                    self._local_live_observation = {
                        "worker_node_id": self.node_id,
                        "completed_at": time.time(),
                        "device": frame.result.get("device", ""),
                    }
            except Exception as exc:
                frame.error = exc
                with self._observation_lock:
                    self._local_live_observation = {}
                with self._lock:
                    self._close_session(identity)
            finally:
                frame.done.set()

    def analyze(
        self,
        image,
        *,
        task: Literal["motion_description", "printer_analysis", "labeling"] = "motion_description",
        camera_id: str = "",
        artifact: ArtifactRef | None = None,
        local_only: bool = False,
        backend_hint: str = "",
    ) -> dict:
        plan = self._plan(task, local_only=local_only)
        uses_ai = task == "printer_analysis" or (
            (backend_hint == "ollama" or (task == "motion_description" and self.config.ai.enabled))
            and not plan.selected_target.model
        )
        if uses_ai and self.config.ai.provider != "ollama" and not plan.selected_target.provider_id:
            raise JobError(
                "unsupported_provider", "Isolated workers do not support this AI provider."
            )
        if artifact is None:
            evidence = self.storage.put_bytes(
                "analysis_evidence", self.jpeg(image), camera_id=camera_id, mime_type="image/jpeg"
            )
            artifact = ArtifactRef.from_artifact(evidence, "image")
        parameters = (
            {"backend": "ollama"}
            if task == "printer_analysis"
            or (task == "motion_description" and self.config.ai.enabled)
            else {}
        )
        if backend_hint in {"florence2", "qwen2.5-vl"} and not plan.selected_target.model:
            parameters["backend"] = backend_hint
        if backend_hint == "ollama" and not plan.selected_target.model:
            parameters["backend"] = "ollama"
        elif (backend_hint.startswith("model:") or backend_hint == "builtin") and (
            plan.selected_target.node_id == self.node_id and not plan.selected_target.model
        ):
            plan.selected_target.model = backend_hint
        spec = JobSpec(
            task=task,
            origin_node_id=self.node_id,
            parameters=parameters,
            input_artifacts=[artifact],
            placement_plan=plan,
            resource_budget=plan.budget,
            priority=80,
            reference={"camera_id": camera_id},
        )
        record = self.jobs.submit(spec, idempotency_key=spec.job_id, principal_scope=task)
        deadline = min(record.deadline_at, time.time() + 60)
        while time.time() < deadline:
            current = self.jobs.get(record.job_id)
            if current is not None and current.state == "succeeded":
                return current.stages[-1].result
            if current is None or current.state in {"failed", "cancelled", "deadline_exceeded"}:
                raise JobError(
                    "inference_unavailable", "Selected analysis worker could not answer."
                )
            if self._closed.wait(0.05):
                break
        self.jobs.request_cancel(record.job_id)
        raise JobError("deadline_exceeded", "Analysis exceeded its response deadline.")


class RoutedLabeler:
    def __init__(self, workloads: WorkloadService, backend: str) -> None:
        self.workloads, self.backend = workloads, backend

    def info(self):
        from tailcam.activelearning.backends import BackendInfo

        if self.backend not in {"builtin", "ollama", "florence2", "qwen2.5-vl"} and not (
            self.backend.startswith("model:") and self.backend[6:].isdecimal()
        ):
            raise ValueError("unknown labeling model")
        self.workloads._plan("labeling")
        return BackendInfo(
            "workload",
            "Placed labeling worker",
            "detector",
            True,
            "Execution availability is checked by the assigned worker",
        )

    def predict(self, image):
        from tailcam.ai.analyzer import Detection

        try:
            result = self.workloads.analyze(image, task="labeling", backend_hint=self.backend)
            predictions = result.get("predictions", [])
            boxes = [
                Detection(**box)
                for prediction in predictions
                for box in prediction.get("boxes", [])
            ]
            for prediction in predictions:
                if prediction.get("label") not in {None, "nothing"}:
                    boxes.append(
                        Detection(prediction["label"], prediction["confidence"], 0.5, 0.5, 1.0, 1.0)
                    )
            return boxes
        except (JobError, ExecutionError):
            return None
