"""Timelapse frame references and backwards-compatible durable-job projection."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tailcam.jobs.models import ArtifactRef, JobError, JobSpec, OutputSlot, StageSpec, TaskKind
from tailcam.media.storage import alias, local_path
from tailcam.storage.models import ContentKind


def submit(service, tl_id: int, task: TaskKind, parameters: dict):
    storage, jobs = service._storage_service, service._job_service
    record = service._store.get_timelapse(tl_id)
    if storage is None or record is None:
        raise JobError("input_unavailable", "Timelapse artifact storage is unavailable.")
    variant = "encode" if task == "timelapse_encode" else "interpolate"
    saved = storage.catalog.setting(f"timelapse_job:{tl_id}:{variant}")
    if saved:
        prior = jobs.get(saved)
        if prior and prior.state not in {"succeeded", "failed", "cancelled", "deadline_exceeded"}:
            return prior
    frames = [
        entry
        for entry in storage.catalog.aliases("timelapse", str(tl_id))
        if entry["variant"].startswith("frame/")
    ]
    if not frames:
        for index, path in enumerate(sorted(Path(record.frames_dir).glob("*.jpg"))):
            artifact = storage.adopt_existing(
                path,
                "timelapse_frame",
                namespace="timelapse",
                legacy_id=str(tl_id),
                variant=f"frame/{index:06d}",
                camera_id=record.camera_id,
                metadata={"frame_index": index},
            )
            frames.append({"variant": f"frame/{index:06d}", "artifact_id": artifact.artifact_id})
    if not frames or len(frames) > 10000:
        raise JobError("invalid_input", "Encoding requires between 1 and 10000 frame artifacts.")
    refs = []
    origin = storage.node_id
    for frame in sorted(frames, key=lambda item: item["variant"]):
        artifact = storage.catalog.get(frame["artifact_id"])
        if artifact is None or artifact.state not in {"committed", "replicated"}:
            raise JobError(
                "input_unavailable", "A captured frame has not reached its storage owner."
            )
        origin = artifact.origin_node_id
        refs.append(ArtifactRef.from_artifact(artifact, frame["variant"].replace("/", "_")))
    admitted = service._storage_jobs.get(tl_id)
    policy = storage.get_policy().model_copy(deep=True)
    output_kinds: dict[str, ContentKind] = (
        {"video": "timelapse_video", "thumbnail": "thumbnail"}
        if task == "timelapse_encode"
        else {"smooth": "timelapse_smooth"}
    )
    outputs = [
        OutputSlot(
            slot=slot,
            kind=kind,
            mime_type="image/jpeg" if kind == "thumbnail" else "video/mp4",
            admission=(
                admitted.plans[kind]
                if admitted is not None
                else storage.admit(
                    kind,
                    origin_node_id=origin,
                    camera_id=record.camera_id,
                    policy_snapshot=policy,
                )
            ),
        )
        for slot, kind in output_kinds.items()
    ]
    placement_policy = jobs.get_policy().model_copy(deep=True)
    plan = jobs.placement.plan(task, policy_snapshot=placement_policy)
    spec = JobSpec(
        task=task,
        origin_node_id=origin,
        parameters=parameters,
        input_artifacts=refs,
        outputs=outputs,
        placement_plan=plan,
        resource_budget=plan.budget,
        priority=30,
        reference={"timelapse_id": str(tl_id), "variant": variant, "camera_id": record.camera_id},
    )
    if task == "timelapse_encode" and record.auto_smooth:
        smooth_plan = jobs.placement.plan("timelapse_interpolate", policy_snapshot=placement_policy)
        smooth_output = OutputSlot(
            slot="smooth",
            kind="timelapse_smooth",
            mime_type="video/mp4",
            admission=(
                admitted.plans["timelapse_smooth"]
                if admitted is not None
                else storage.admit(
                    "timelapse_smooth",
                    origin_node_id=origin,
                    camera_id=record.camera_id,
                    policy_snapshot=policy,
                )
            ),
        )
        spec.stages = [
            StageSpec(
                stage_id="encode",
                task=task,
                parameters=parameters,
                input_artifacts=refs,
                outputs=outputs,
                placement_plan=plan,
                budget=plan.budget,
            ),
            StageSpec(
                stage_id="interpolate",
                task="timelapse_interpolate",
                input_artifacts=refs,
                depends_on=["encode"],
                outputs=[smooth_output],
                placement_plan=smooth_plan,
                budget=smooth_plan.budget,
                parameters={
                    "fps": record.output_fps,
                    "target_fps": record.smooth_target_fps,
                    "interpolate": record.smooth_interpolate,
                    "deflicker": record.smooth_deflicker,
                    "engine": record.smooth_engine or "ffmpeg",
                    "quality": record.smooth_quality,
                },
            ),
        ]
    result = jobs.submit(spec, idempotency_key=spec.job_id, principal_scope="timelapse")
    storage.catalog.set_setting(f"timelapse_job:{tl_id}:{variant}", result.job_id)
    if spec.stages:
        storage.catalog.set_setting(f"timelapse_job:{tl_id}:interpolate", result.job_id)
    # Every frame now has a durable owner. Encoding is free to happen on a third node.
    if admitted is not None:
        admitted.release()
        service._storage_jobs.pop(tl_id, None)
    return result


def project(service, tl_id: int) -> None:
    if service._job_service is None or service._storage_service is None:
        return
    for variant in ("encode", "interpolate"):
        saved = service._storage_service.catalog.setting(f"timelapse_job:{tl_id}:{variant}")
        job = service._job_service.get(saved) if saved else None
        if job is None:
            continue
        task = "timelapse_encode" if variant == "encode" else "timelapse_interpolate"
        stage = next((stage for stage in job.stages if stage.task == task), None)
        if stage is None:
            continue
        active = service._encoding if variant == "encode" else service._smoothing
        if stage.state in {"succeeded", "failed", "cancelled", "deadline_exceeded"}:
            active.discard(tl_id)
        else:
            active.add(tl_id)
        projection_key = f"timelapse_projected:{tl_id}:{variant}"
        revision = f"{job.job_id}:{job.revision}"
        if service._storage_service.catalog.setting(projection_key) == revision:
            continue
        if stage.state == "succeeded":
            changes: dict[str, Any] = {}
            for ref in stage.outputs:
                artifact = service._storage_service.catalog.get(ref.artifact_id)
                if artifact is None:
                    return
                alias(service._storage_service, "timelapse", tl_id, ref.slot, artifact)
                path = local_path(service._storage_service, artifact)
                if ref.slot == "video":
                    changes.update(video_path=path, size_bytes=artifact.size_bytes)
                elif ref.slot == "thumbnail":
                    changes["thumb_path"] = path or None
                elif ref.slot == "smooth":
                    changes.update(smooth_path=path, smooth_size_bytes=artifact.size_bytes)
            if variant == "encode":
                changes.update(
                    state="complete",
                    width=stage.result.get("width", 0),
                    height=stage.result.get("height", 0),
                    frames_captured=stage.result.get("frames", 0),
                    end_ts=stage.ended_at or time.time(),
                )
                service._encoding.discard(tl_id)
            else:
                changes.update(
                    smooth_state="complete", smooth_engine=stage.result.get("engine", "")
                )
                service._smoothing.discard(tl_id)
            service._store.update_timelapse(tl_id, **changes)
        elif stage.state in {"failed", "cancelled", "deadline_exceeded"}:
            service._store.update_timelapse(
                tl_id, **({"state": "error"} if variant == "encode" else {"smooth_state": "error"})
            )
            (service._encoding if variant == "encode" else service._smoothing).discard(tl_id)
        else:
            (service._encoding if variant == "encode" else service._smoothing).add(tl_id)
            service._store.update_timelapse(
                tl_id,
                **(
                    {"state": "encoding"} if variant == "encode" else {"smooth_state": "processing"}
                ),
            )
        service._storage_service.catalog.set_setting(projection_key, revision)
