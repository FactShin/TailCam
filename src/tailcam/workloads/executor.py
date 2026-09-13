"""Parent-side execution and fenced publication of child-staged artifacts."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from tailcam.jobs.models import (
    ArtifactRef,
    JobError,
    Lease,
    Progress,
    PublicationPermit,
    ResourceBudget,
    ResultManifest,
    SafeError,
)
from tailcam.storage.models import StorageError
from tailcam.workloads.handlers import safe_path
from tailcam.workloads.process import ExecutionError, ProcessRunner


def validate_budget(budget: ResourceBudget) -> None:
    if budget.cpu_seconds < budget.wall_seconds * budget.cpu_threads:
        raise JobError(
            "unsupported_cpu_budget",
            "CPU allowance must cover wall seconds multiplied by admitted CPU threads; "
            "this worker does not meter process-tree CPU consumption.",
        )
    if budget.require_hard_memory_limit or getattr(budget, "require_hard_workspace_limit", False):
        raise JobError(
            "unsupported_hard_limit",
            "This worker enforces process deadlines and admission reservations; "
            "it does not provide a hard memory or aggregate filesystem quota.",
        )
    if budget.output_bytes > budget.workspace_bytes:
        raise JobError("invalid_budget", "Output budget cannot exceed the workspace budget.", 422)


class _Cancellation:
    def __init__(self, service: Any, lease: Lease, closed: threading.Event) -> None:
        self.service, self.lease, self.closed = service, lease, closed

    def is_set(self) -> bool:
        return self.closed.is_set() or self.service.is_cancel_requested(self.lease)


class ProcessExecutor:
    supports_hard_memory_limit = False

    def __init__(self, storage_service: Any, *, node_id: str, config: Any = None) -> None:
        self.storage = storage_service
        self.node_id, self.config = node_id, config
        self.runner = ProcessRunner()
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()

    def _runtime(self) -> dict[str, Any]:
        """Only server-owned config may select an upstream URL or executable."""
        if self.config is None:
            return {}
        ai = self.config.ai
        return {
            "ai": {
                "enabled": ai.enabled,
                "provider": ai.provider,
                "base_url": ai.base_url,
                "model": ai.model,
                "timeout": ai.timeout,
                "prompt": ai.prompt,
            },
            "rife_path": self.config.timelapse.rife_path,
            "rife_model": self.config.timelapse.rife_model,
        }

    def execute(self, lease: Lease, service: Any) -> None:
        workspace = None
        prepared = False
        try:
            if lease.worker_node_id != self.node_id:
                raise JobError("wrong_worker", "Assignment is not for this node.", 403)
            plan = lease.spec.placement_plan
            if plan is not None and plan.selected_target.node_id not in {None, self.node_id}:
                raise JobError("wrong_worker", "An executor cannot forward assignments.", 403)
            validate_budget(lease.spec.budget)
            service.start_attempt(lease)
            workspace = self.storage.workspace_for_task(
                lease.spec.task,
                max_bytes=lease.spec.budget.workspace_bytes,
                admission=lease.spec.workspace_admission,
            )
            self.storage.catalog.set_setting(
                f"job_workspace:{lease.attempt_id}", str(workspace.path)
            )
            service.set_workspace(lease, str(workspace.path))
            manifest = self.run(lease, workspace, service)
            permit = service.prepare_result(lease, manifest)
            prepared = True
            artifacts = self.publish(
                permit,
                workspace.path,
                camera_id=service.spec(permit.job_id).reference.get("camera_id", ""),
            )
            service.commit_result(permit, artifacts)
            workspace.release()
            self.storage.catalog.set_setting(f"job_workspace:{lease.attempt_id}", "")
        except Exception as exc:
            if isinstance(exc, (JobError, StorageError, ExecutionError)):
                code = exc.code
            else:
                code = "worker_failed"
            error = SafeError(
                code=code,
                detail={
                    "cancelled": "Worker process was stopped.",
                    "deadline_exceeded": "Worker exceeded its admitted deadline.",
                    "engine_unavailable": "The selected worker engine or model is unavailable.",
                    "inference_unavailable": "The selected inference engine could not answer.",
                    "unsupported_hard_limit": "The requested hard limit cannot be enforced.",
                    "unsupported_provider": "This AI provider is unsupported by isolated workers.",
                }.get(code, "The worker could not complete this operation."),
                retryable=code
                in {
                    "owner_unavailable",
                    "worker_failed",
                    "transfer_interrupted",
                    "peer_unavailable",
                },
            )
            try:
                service.fail(lease, error)
            finally:
                if workspace is not None and not prepared:
                    workspace.release()
                    self.storage.catalog.set_setting(f"job_workspace:{lease.attempt_id}", "")

    def run(self, lease: Lease, workspace: Any, service: Any) -> ResultManifest:
        budget = lease.spec.budget
        validate_budget(budget)
        if sum(item.size_bytes for item in lease.spec.input_artifacts) > budget.workspace_bytes:
            raise JobError("workspace_full", "Inputs exceed the worker scratch budget.")
        selected = lease.spec.placement_plan.selected_target if lease.spec.placement_plan else None
        uses_ai = lease.spec.task == "printer_analysis" or (
            lease.spec.parameters.get("backend") == "ollama" and not (selected and selected.model)
        )
        if (
            uses_ai
            and self.config is not None
            and self.config.ai.provider != "ollama"
            and not (selected and selected.provider_id)
        ):
            raise JobError(
                "unsupported_provider", "Isolated workers do not support this AI provider."
            )
        inputs = []
        for ref in lease.spec.input_artifacts:
            if service.is_cancel_requested(lease):
                raise ExecutionError("cancelled", "Worker was cancelled.")
            path = self.storage.materialize_ref(ref, workspace, deadline_at=lease.deadline_at)
            inputs.append({"slot": ref.slot, "path": path.relative_to(workspace.path).as_posix()})
        parameters = dict(lease.spec.parameters)
        provider = parameters.pop("_provider", None)
        runtime = self._runtime()
        plan = lease.spec.placement_plan
        if plan is not None and plan.selected_target.provider_id:
            from tailcam.jobs.models import ProviderDefinition

            definition = ProviderDefinition.model_validate(provider)
            if definition.provider_id != plan.selected_target.provider_id:
                raise JobError("provider_conflict", "Provider snapshot does not match admission.")
            runtime["ai"] = {
                "enabled": True,
                "provider": "ollama",
                "base_url": definition.base_url,
                "model": plan.selected_target.model or definition.model,
                "timeout": min(60, budget.wall_seconds),
            }
            parameters["backend"] = "ollama"
        elif (
            lease.spec.task in {"labeling", "motion_description"}
            and (parameters.get("backend") != "ollama" or (plan and plan.selected_target.model))
            and not any(item["slot"] == "model" for item in inputs)
        ):
            from tailcam.workloads.models import local_model_inputs

            if self.config is None:
                raise JobError("model_unavailable", "Worker model configuration is unavailable.")
            model_parameters, model_inputs = local_model_inputs(
                self.storage,
                self.storage.store,
                self.config,
                (plan.selected_target.model if plan else "")
                or (
                    "backend:" + str(parameters["backend"])
                    if parameters.get("backend") in {"florence2", "qwen2.5-vl"}
                    else ""
                ),
                workspace,
                lease.spec.task,
                deadline_at=lease.deadline_at,
            )
            model_parameters.pop("_model_name", None)
            parameters.update(model_parameters)
            inputs.extend(model_inputs)
        request = {
            "task": lease.spec.task,
            "parameters": parameters,
            "inputs": inputs,
            "runtime": runtime,
            "workspace_bytes": budget.workspace_bytes,
            "output_bytes": budget.output_bytes,
            "cpu_threads": budget.cpu_threads,
            "gpu_slots": budget.gpu_slots,
            "output_slots": [slot.slot for slot in lease.spec.outputs],
        }
        raw = self.runner.run(
            request,
            workspace.path,
            deadline=min(lease.deadline_at, time.time() + budget.wall_seconds),
            cancel=_Cancellation(service, lease, self._closed),  # type: ignore[arg-type]
            on_progress=lambda value: service.heartbeat(lease, Progress.model_validate(value)),
            check_workspace=workspace.check,
        )
        manifest = ResultManifest.model_validate(raw)
        self._verify_manifest(manifest, lease.spec.outputs, workspace.path, budget.output_bytes)
        workspace.check()
        if parameters.get("builtin"):
            from tailcam.workloads.models import persist_builtin

            persist_builtin(workspace.path, str(parameters["builtin"]))
        return manifest

    @staticmethod
    def _verify_manifest(manifest: ResultManifest, slots: list, root: Path, maximum: int) -> None:
        expected = {slot.slot for slot in slots}
        found = [output.slot for output in manifest.outputs]
        if set(found) != expected or len(found) != len(set(found)):
            raise ExecutionError("invalid_manifest", "Worker output slots do not match admission.")
        total = 0
        for output in manifest.outputs:
            path = safe_path(root, output.path)
            if not path.is_file():
                raise ExecutionError("invalid_manifest", "Output is not a regular file.")
            size = 0
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if total > maximum:
                        raise ExecutionError("invalid_manifest", "Output budget exceeded.")
                    digest.update(chunk)
            if size != output.size_bytes or digest.hexdigest() != output.sha256:
                raise ExecutionError("invalid_manifest", "Output checksum did not match.")

    def publish(
        self, permit: PublicationPermit, root: Path, *, camera_id: str = ""
    ) -> list[ArtifactRef]:
        self._verify_manifest(
            permit.manifest,
            permit.slots,
            root,
            sum(output.size_bytes for output in permit.manifest.outputs),
        )
        slots = {slot.slot: slot for slot in permit.slots}
        artifacts = []
        for output in permit.manifest.outputs:
            slot = slots[output.slot]
            if slot.admission is None or slot.artifact_id is None:
                raise JobError("invalid_permit", "Output publication was not admitted.")
            existing = self.storage.catalog.get(slot.artifact_id)
            if existing is not None and existing.state in {"committed", "replicated"}:
                if (existing.sha256, existing.size_bytes) != (output.sha256, output.size_bytes):
                    raise JobError("output_conflict", "A committed output has different content.")
                artifact = existing
            else:
                artifact = self.storage.finalize(
                    safe_path(root, output.path),
                    slot.kind,
                    admission=slot.admission,
                    artifact_id=slot.artifact_id,
                    origin_node_id=permit.origin_node_id,
                    camera_id=camera_id,
                    parent_id=(
                        slots["video"].artifact_id
                        if output.slot == "thumbnail" and "video" in slots
                        else None
                    ),
                    mime_type=slot.mime_type,
                    metadata={
                        "job_id": permit.job_id,
                        "stage_id": permit.stage_id,
                        "slot": output.slot,
                        **(
                            {"format": "directory-zip"}
                            if permit.manifest.result.get("format") == "directory-zip"
                            else {}
                        ),
                    },
                )
                if artifact.state not in {"committed", "replicated"}:
                    raise StorageError("owner_unavailable", "Output delivery is not committed.")
            artifacts.append(ArtifactRef.from_artifact(artifact, output.slot))
        return artifacts

    def recover_commit(self, permit: PublicationPermit, service: Any) -> None:
        saved = service.get_workspace(permit.attempt_id)
        if not saved:
            raise JobError("staging_missing", "Selected attempt staging is unavailable.")
        root = Path(saved)
        artifacts = self.publish(
            permit, root, camera_id=service.spec(permit.job_id).reference.get("camera_id", "")
        )
        service.commit_result(permit, artifacts)
        self.storage.release_workspace(root)
        self.storage.catalog.set_setting(f"job_workspace:{permit.attempt_id}", "")

    recover = recover_commit
