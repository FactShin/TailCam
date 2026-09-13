"""Producer adapters for frozen storage plans and explicit temporary workspaces.

Byte writes are checked before touching disk. External encoders and ML runtimes
use reserved, monitored workspaces; these are not operating-system disk quotas.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import cv2

from tailcam.storage.models import StorageError
from tailcam.streaming.encoder import encode_jpeg


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "camera"


def enabled(service: Any) -> bool:
    return service is not None and service.enabled is True


def image_bound(image: Any) -> int:
    """Conservative JPEG admission size; the actual bytes are checked on ingest."""
    return int(image.nbytes) * 2 + 65536


def thumbnail_bytes(image: Any, width: int = 320, quality: int = 75) -> bytes:
    height, source_width = image.shape[:2]
    target_width = min(width, max(1, source_width))
    thumb = cv2.resize(
        image, (target_width, max(1, int(height * target_width / max(1, source_width))))
    )
    return encode_jpeg(thumb, quality)


def local_path(service: Any, artifact: Any) -> str:
    """Legacy records expose a local path only when this node owns the bytes."""
    if artifact.owner_node_id != service.node_id:
        return ""
    return str(service.resolve(artifact.artifact_id))


def alias(service: Any, namespace: str, legacy_id: int, variant: str, artifact: Any) -> None:
    service.catalog.alias(namespace, str(legacy_id), variant, artifact.artifact_id)


class ProducerWorkspace:
    """A job's destination choices and finite workspace, admitted before launch."""

    def __init__(
        self,
        service: Any,
        kinds: tuple[str, ...],
        camera_id: str = "",
        origin_node_id: str | None = None,
    ) -> None:
        self.service = service
        policy = service.get_policy().model_copy(deep=True)
        # Four concurrent jobs can each reserve an explicit share; the service
        # enforces the aggregate cap atomically, including retained failures.
        self.max_bytes = min(policy.workspace_max_bytes // 4, policy.artifact_max_bytes)
        self.camera_id = camera_id
        self.origin_node_id = origin_node_id
        self.plans = {
            kind: service.admit(
                kind,
                camera_id=camera_id,
                origin_node_id=origin_node_id,
                requires_workspace=True,
                policy_snapshot=policy,
            )
            for kind in kinds
        }
        # The workspace admission is separate from sibling artifact admissions.
        self.lease = service.workspace(
            kinds[0],
            max_bytes=self.max_bytes,
            camera_id=camera_id,
            origin_node_id=origin_node_id,
            admission=self.plans[kinds[0]],
        )
        self.path = self.lease.path

    def check(self, additional: int = 0) -> int:
        used = self.lease.check()
        if used + additional > self.max_bytes:
            raise StorageError(
                "workspace_full", "The job's temporary workspace budget is exhausted"
            )
        return used

    def write(self, path: Path, data: bytes) -> None:
        self.check(len(data))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def finish(self, path: Path, kind: str, **kwargs: Any) -> Any:
        self.check()
        return self.service.finalize(
            path,
            kind,
            admission=self.plans[kind],
            camera_id=self.camera_id,
            origin_node_id=self.origin_node_id,
            **kwargs,
        )

    def put(self, kind: str, data: bytes, **kwargs: Any) -> Any:
        return self.service.put_bytes(
            kind,
            data,
            admission=self.plans[kind],
            camera_id=self.camera_id,
            origin_node_id=self.origin_node_id,
            **kwargs,
        )

    def release(self) -> None:
        self.lease.release(remove=True)


def store_image(
    service: Any,
    kind: str,
    image: Any,
    *,
    camera_id: str = "",
    quality: int = 88,
    metadata: dict[str, Any] | None = None,
    thumb_width: int = 240,
    policy_snapshot: Any = None,
) -> tuple[Any, Any]:
    """Admit both siblings before encoding or writing either one."""
    policy = (
        policy_snapshot
        if policy_snapshot is not None
        else service.get_policy().model_copy(deep=True)
    )
    plan = service.admit(
        kind, camera_id=camera_id, expected_bytes=image_bound(image), policy_snapshot=policy
    )
    thumb_plan = service.admit(
        "thumbnail", camera_id=camera_id, expected_bytes=image_bound(image), policy_snapshot=policy
    )
    artifact = service.put_bytes(
        kind,
        encode_jpeg(image, quality),
        admission=plan,
        camera_id=camera_id,
        metadata=metadata,
        mime_type="image/jpeg",
    )
    thumb = service.put_bytes(
        "thumbnail",
        thumbnail_bytes(image, thumb_width),
        admission=thumb_plan,
        camera_id=camera_id,
        parent_id=artifact.artifact_id,
        metadata=metadata,
        mime_type="image/jpeg",
    )
    return artifact, thumb


def sample_path(job: ProducerWorkspace, sample: Any) -> Path:
    artifact = job.service.catalog.resolve_alias("sample", str(sample.id), "file")
    if artifact is not None:
        return job.service.materialize(artifact.artifact_id, job.lease)
    return Path(sample.path)


def bounded_copy(job: ProducerWorkspace, source: str | Path, target: str | Path) -> None:
    source, target = Path(source), Path(target)
    job.check(source.stat().st_size)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as inp, target.open("xb") as out:
        while chunk := inp.read(1024 * 1024):
            job.check(len(chunk))
            out.write(chunk)


def archive_tree(job: ProducerWorkspace, source: Path, target: Path) -> Path:
    """A portable stored ZIP, with no symlinks and prechecked member bytes.

    ZIP_STORED avoids compression work and makes an upper bound available
    before the archive is opened. The encoder's own output remains monitored.
    """
    import zipfile

    entries = sorted(source.rglob("*"))
    if len(entries) > 10000 or source.is_symlink():
        raise StorageError("unsafe_output", "Managed output has too many entries or an unsafe root")
    for entry in entries:
        if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
            raise StorageError("unsafe_output", "Managed output contains a non-regular file")
    files = [entry for entry in entries if entry.is_file()]
    total = 0
    for item in files:
        if item.is_symlink() or not item.is_file():
            raise StorageError("unsafe_output", "A managed output contains a symbolic link")
        total += item.stat().st_size + len(item.relative_to(source).as_posix().encode()) * 2 + 256
    job.check(total + 1024)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for item in files:
            archive.write(item, item.relative_to(source).as_posix())
    job.check()
    return target
