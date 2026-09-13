"""Approved-node job delivery with bounded responses and acceptance reconciliation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from uuid import UUID, uuid5

import httpx

from tailcam.jobs.models import ArtifactRef, JobError, JobRecord, JobSpec, SafeError
from tailcam.storage.models import ArtifactPin


class JobTransport:
    def __init__(self, resolve_peer: Callable[[str], str | None], *, client=None):
        self.resolve_peer, self._client = resolve_peer, client

    def request(self, node_id: str, method: str, path: str, **kwargs):
        from urllib.parse import urlsplit

        base = self.resolve_peer(node_id)
        if base is None:
            raise JobError("worker_unavailable", "Approved worker is unavailable.", 503)
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise JobError("worker_unavailable", "Approved worker address is invalid.", 503)
        client = self._client or httpx.Client(timeout=3, trust_env=False, follow_redirects=False)
        try:
            with client.stream(
                method, base.rstrip("/") + path, headers={"Accept-Encoding": "identity"}, **kwargs
            ) as response:
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise JobError(
                        "invalid_worker_response", "Encoded worker responses are not accepted.", 502
                    )
                body = bytearray()
                for chunk in response.iter_bytes(65536):
                    if len(body) + len(chunk) > 2 * 1024**2:
                        raise JobError(
                            "invalid_worker_response", "Worker response exceeds its limit.", 502
                        )
                    body.extend(chunk)
                if response.status_code >= 300:
                    raise JobError(
                        "worker_unavailable",
                        "Worker rejected the operation.",
                        503 if response.status_code >= 500 else 409,
                    )
                return json.loads(body)
        except (httpx.HTTPError, OSError):
            raise JobError("worker_unavailable", "Worker could not be reached.", 503) from None
        except (ValueError, TypeError):
            raise JobError(
                "invalid_worker_response", "Worker returned invalid data.", 502
            ) from None
        finally:
            if self._client is None:
                client.close()

    def execute(self, lease, service):
        """A remote worker owns its durable attempts after accepting this immutable stage.

        Lost acknowledgement always reconciles this same remote UUID. A network partition
        never authorizes a second node to execute the accepted stage.
        """
        service.start_attempt(lease)
        source_spec = service.spec(lease.job_id)
        remote_id = str(uuid5(UUID(lease.job_id), "remote-stage:" + lease.stage_id))
        stage = lease.spec.model_copy(deep=True)
        stage.depends_on = []
        reference = {
            "coordinator_job_id": lease.job_id,
            "coordinator_stage_id": lease.stage_id,
        }
        if camera_id := source_spec.reference.get("camera_id"):
            # The publishing worker needs the original camera as well as its node UUID.
            reference["camera_id"] = camera_id
        remote_spec = JobSpec(
            job_id=remote_id,
            task=stage.task,
            origin_node_id=source_spec.origin_node_id,
            stages=[stage],
            resource_budget=stage.budget,
            deadline_at=lease.deadline_at,
            max_attempts=source_spec.max_attempts,
            priority=source_spec.priority,
            reference=reference,
        )
        key = "remote_assignment:" + lease.job_id + ":" + lease.stage_id
        with service.journal.transaction() as conn:
            saved = service.journal.setting(key)
            if saved is None:
                service.journal.set_setting(key, remote_spec.model_dump(mode="json"), conn)
            else:
                remote_spec = JobSpec.model_validate(saved)
        accepted = False
        while service.clock() < lease.deadline_at:
            record = service.get(lease.job_id)
            cancelling = record is None or record.cancel_requested or service._stop.is_set()
            try:
                if cancelling:
                    try:
                        self.request(
                            lease.worker_node_id, "POST", f"/api/v1/jobs/{remote_id}/cancel"
                        )
                    except JobError:
                        pass
                    payload = self.request(lease.worker_node_id, "GET", f"/api/v1/jobs/{remote_id}")
                elif not accepted:
                    payload = self.request(
                        lease.worker_node_id,
                        "POST",
                        "/api/v1/jobs/execute",
                        json={
                            "spec": remote_spec.model_dump(mode="json"),
                            "coordinator_node_id": service.node_id,
                        },
                    )
                    accepted = True
                else:
                    payload = self.request(lease.worker_node_id, "GET", f"/api/v1/jobs/{remote_id}")
                remote = JobRecord.model_validate(payload)
                if remote.job_id != remote_id or remote.coordinator_node_id != service.node_id:
                    raise JobError(
                        "invalid_worker_response", "Worker returned another job identity.", 502
                    )
                service.heartbeat(lease, remote.stages[0].progress)
                if remote.state == "succeeded":
                    refs = [ref for record in remote.stages for ref in record.outputs]
                    service.commit_remote(lease, refs, remote.stages[-1].result)
                    return
                if remote.state in {"failed", "cancelled", "deadline_exceeded"}:
                    service.fail(
                        lease,
                        SafeError(
                            code="remote_task_failed",
                            detail="Remote worker ended without a successful result",
                        ),
                    )
                    return
            except JobError as exc:
                if exc.status_code < 500 and not cancelling:
                    service.fail(
                        lease,
                        SafeError(
                            code="remote_admission_refused",
                            detail="Remote worker refused task admission",
                        ),
                    )
                    return
                # Retain the lease while acceptance is unknown, avoiding duplicate execution.
            except (ValueError, TypeError):
                service.fail(
                    lease,
                    SafeError(
                        code="invalid_worker_response",
                        detail="Worker returned an invalid job record",
                    ),
                )
                return
            time.sleep(0.25)
            if service._stop.is_set():
                # Leave the durable assignment for restart reconciliation, never assert remote stop.
                return
        try:
            self.request(lease.worker_node_id, "POST", f"/api/v1/jobs/{remote_id}/cancel")
        except JobError:
            pass  # The immutable remote deadline still bounds that worker independently.
        service.fail(
            lease, SafeError(code="deadline_exceeded", detail="Remote worker deadline elapsed")
        )

    def pin_artifact(self, reference: ArtifactRef, pin: ArtifactPin) -> ArtifactPin:
        result = ArtifactPin.model_validate(
            self.request(
                reference.owner_node_id,
                "POST",
                f"/api/v1/artifacts/{reference.artifact_id}/pins",
                json=pin.model_dump(mode="json"),
            )
        )
        if result != pin:
            raise JobError("invalid_pin_response", "Owner returned a different artifact hold.", 502)
        return result

    def release_artifact_pin(self, reference: ArtifactRef, pin: ArtifactPin) -> None:
        self.request(
            reference.owner_node_id,
            "DELETE",
            f"/api/v1/artifacts/{reference.artifact_id}/pins/{pin.pin_id}",
            params={"coordinator_node_id": pin.coordinator_node_id},
        )
