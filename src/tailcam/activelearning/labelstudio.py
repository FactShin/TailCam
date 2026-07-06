"""Label Studio integration via the official Python SDK (label-studio-sdk).

The SDK import is lazy and optional — TailCam runs fine without it, and the
UI explains how to install it (``pip install 'tailcam[activelearning]'``).
All SDK calls are wrapped so a down server, bad token, or missing project
becomes a friendly error string instead of a traceback. The API token is never
logged.

Images are imported inline as base64 data URIs, so no shared storage or
serving configuration is needed and it works identically on Linux and macOS
(TailCam and Label Studio don't even need to share a filesystem). Each task
carries ``data.meta.tailcam_image_path`` — the stable key used to match
completed annotations back to the originating sample on sync, independent of
task ids.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from tailcam.activelearning.annotations import (
    AnnotatedFrame,
    FrameAnnotation,
    from_label_studio_result,
    label_config_xml,
    to_label_studio_task,
)
from tailcam.config import ActiveLearningConfig
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

# Don't inline-import absurdly large files (a data URI ~4/3 of this).
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


class LabelStudioError(RuntimeError):
    """A Label Studio operation failed; the message is safe to show verbatim."""


@dataclass
class LabelStudioStatus:
    sdk_installed: bool
    configured: bool  # URL + token present
    connected: bool
    url: str
    project_id: int = 0
    project_name: str = ""
    error: str = ""


@dataclass
class CompletedTask:
    task_id: int
    image_path: str  # data.meta.tailcam_image_path
    annotations: list[FrameAnnotation] = field(default_factory=list)


def sdk_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("label_studio_sdk") is not None


class LabelStudioService:
    """Thin, defensive wrapper around the Label Studio SDK.

    ``client_factory`` exists for tests: it must return an object shaped like
    ``label_studio_sdk.client.LabelStudio`` (``.projects`` / ``.tasks``).
    """

    def __init__(self, config: ActiveLearningConfig, client_factory=None) -> None:
        self._config = config
        self._client_factory = client_factory
        self._client = None
        self._client_key: tuple[str, str] | None = None

    # -- connection ----------------------------------------------------------
    def _get_client(self):
        cfg = self._config
        if not cfg.label_studio_url.strip():
            raise LabelStudioError("Label Studio URL is not set")
        if not cfg.label_studio_token.strip():
            raise LabelStudioError(
                "Label Studio API token is not set — copy it from "
                "Account & Settings → Access Token in Label Studio"
            )
        key = (cfg.label_studio_url.strip(), cfg.label_studio_token.strip())
        if self._client is not None and self._client_key == key:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory(*key)
        else:
            if not sdk_installed():
                raise LabelStudioError(
                    "the Label Studio SDK is not installed — "
                    "pip install 'tailcam[activelearning]'"
                )
            from label_studio_sdk.client import LabelStudio

            self._client = LabelStudio(base_url=key[0], api_key=key[1])
        self._client_key = key
        return self._client

    def status(self) -> LabelStudioStatus:
        cfg = self._config
        configured = bool(cfg.label_studio_url.strip() and cfg.label_studio_token.strip())
        status = LabelStudioStatus(
            sdk_installed=self._client_factory is not None or sdk_installed(),
            configured=configured,
            connected=False,
            url=cfg.label_studio_url,
            project_id=cfg.project_id,
            project_name=cfg.project_name,
        )
        if not status.sdk_installed:
            status.error = "SDK not installed — pip install 'tailcam[activelearning]'"
            return status
        if not configured:
            status.error = "set the Label Studio URL and API token"
            return status
        try:
            self.check_connection()
            status.connected = True
        except LabelStudioError as exc:
            status.error = str(exc)
        return status

    def check_connection(self) -> None:
        """Raise LabelStudioError when the server is unreachable or the token
        is rejected; return silently when everything works."""
        client = self._get_client()
        try:
            _first_page(client.projects.list(page_size=1))
        except LabelStudioError:
            raise
        except Exception as exc:
            raise LabelStudioError(_friendly_error(exc, self._config.label_studio_url)) from exc

    # -- projects --------------------------------------------------------------
    def list_projects(self) -> list[dict]:
        client = self._get_client()
        try:
            projects = list(client.projects.list())
        except Exception as exc:
            raise LabelStudioError(_friendly_error(exc, self._config.label_studio_url)) from exc
        return [
            {
                "id": int(getattr(p, "id", 0) or 0),
                "title": str(getattr(p, "title", "") or ""),
                "task_count": int(getattr(p, "task_number", 0) or 0),
            }
            for p in projects
        ]

    def ensure_project(self, labels: list[str]) -> int:
        """The configured project's id — validated when set, found by name, or
        created with TailCam's object-detection label config. Persists the id
        back into config so subsequent calls are direct."""
        cfg = self._config
        client = self._get_client()
        if cfg.project_id:
            try:
                client.projects.get(id=cfg.project_id)
                return cfg.project_id
            except Exception as exc:
                raise LabelStudioError(
                    f"project #{cfg.project_id} not found in Label Studio "
                    "— pick another project or clear the project id"
                ) from exc
        for p in self.list_projects():
            if p["title"] == cfg.project_name:
                cfg.project_id = p["id"]
                return p["id"]
        try:
            project = client.projects.create(
                title=cfg.project_name,
                description="TailCam active learning — uncertain frames for human review",
                label_config=label_config_xml(labels),
            )
        except Exception as exc:
            raise LabelStudioError(_friendly_error(exc, cfg.label_studio_url)) from exc
        cfg.project_id = int(getattr(project, "id", 0) or 0)
        log.info("label studio: created project #%s (%s)", cfg.project_id, cfg.project_name)
        return cfg.project_id

    # -- tasks -------------------------------------------------------------------
    def submit_frame(self, project_id: int, frame: AnnotatedFrame, model_id: str) -> int:
        """Import one frame (with the model's predictions as pre-annotations)
        for human review. Returns the created task id, or 0 when the server
        didn't report one — sync matches by image path either way."""
        image_data = _image_data_uri(frame.image_path)
        task = to_label_studio_task(frame, image_data, model_id)
        client = self._get_client()
        try:
            response = client.projects.import_tasks(
                id=project_id, request=[task], return_task_ids=True
            )
        except TypeError:
            # SDK version without return_task_ids
            try:
                response = client.projects.import_tasks(id=project_id, request=[task])
            except Exception as exc:
                raise LabelStudioError(
                    _friendly_error(exc, self._config.label_studio_url)
                ) from exc
        except Exception as exc:
            raise LabelStudioError(_friendly_error(exc, self._config.label_studio_url)) from exc
        task_ids = getattr(response, "task_ids", None) or (
            response.get("task_ids") if isinstance(response, dict) else None
        )
        return int(task_ids[0]) if task_ids else 0

    def pull_completed(self, project_id: int) -> list[CompletedTask]:
        """Every task in the project that has at least one human annotation,
        with regions converted to TailCam's canonical format."""
        client = self._get_client()
        try:
            tasks = list(client.tasks.list(project=project_id, fields="all"))
        except Exception as exc:
            raise LabelStudioError(_friendly_error(exc, self._config.label_studio_url)) from exc
        completed: list[CompletedTask] = []
        for task in tasks:
            annotations = _attr(task, "annotations") or []
            data = _attr(task, "data") or {}
            meta = data.get("meta") if isinstance(data, dict) else {}
            image_path = str((meta or {}).get("tailcam_image_path", ""))
            regions: list[FrameAnnotation] = []
            for ann in annotations:
                if _attr(ann, "was_cancelled"):
                    continue
                regions.extend(from_label_studio_result(_attr(ann, "result") or []))
            if regions and image_path:
                completed.append(
                    CompletedTask(
                        task_id=int(_attr(task, "id") or 0),
                        image_path=image_path,
                        annotations=regions,
                    )
                )
        return completed


def _attr(obj, name: str):
    """Field access that works for SDK models and plain dicts alike."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _first_page(result) -> None:
    """Force one page of a (possibly lazy) paginated SDK response."""
    iterator = iter(result)
    next(iterator, None)


def _image_data_uri(image_path: str) -> str:
    p = Path(image_path)
    if not p.exists():
        raise LabelStudioError(f"frame file missing: {p.name}")
    if p.stat().st_size > _MAX_IMAGE_BYTES:
        raise LabelStudioError(f"frame too large to inline: {p.name}")
    suffix = p.suffix.lower().lstrip(".") or "jpeg"
    mime = {"jpg": "jpeg"}.get(suffix, suffix)
    payload = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{payload}"


def _friendly_error(exc: Exception, url: str) -> str:
    """Map SDK/network failures to actionable text. Never includes the token."""
    text = str(exc)
    status = getattr(exc, "status_code", None)
    if status in (401, 403) or "401" in text or "Unauthorized" in text:
        return "API token rejected — copy a fresh token from Label Studio Account & Settings"
    if status == 404 or "404" in text:
        return "not found — check the project id and Label Studio version"
    if "Connect" in text or "connect" in text or "refused" in text or "timed out" in text:
        return (
            f"cannot reach Label Studio at {url} — is the server running? "
            "Start it with: label-studio start"
        )
    return f"Label Studio error: {text[:300]}"
