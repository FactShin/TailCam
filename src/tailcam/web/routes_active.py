"""REST API for the active learning pipeline (/api/active-learning)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from tailcam.activelearning.backends import (
    list_finetune_backends,
    list_labeling_backends,
    platform_summary,
)
from tailcam.activelearning.labelstudio import LabelStudioError
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.schemas import (
    ActiveLearningInfo,
    ActiveLearningSettings,
    ActiveLearningSyncResult,
    ActiveLearningTrainRequest,
    FinetuneBackendInfo,
    LabelingBackendInfo,
    LabelStudioProjectInfo,
    LabelStudioStatusInfo,
    OkResponse,
    TrainingRunInfo,
)

router = APIRouter(prefix="/api/active-learning", tags=["active-learning"])


def _info(ctx: AppContext) -> ActiveLearningInfo:
    cfg = ctx.config.active_learning
    stats = ctx.active_learning.stats()
    counts = ctx.store.review_counts()
    dataset_id = stats.dataset_id or cfg.dataset_id
    annotated = 0
    version = 1
    if dataset_id:
        dataset = ctx.store.get_dataset(dataset_id)
        if dataset is not None:
            annotated = len(ctx.store.annotation_counts(dataset_id))
            version = dataset.version
    return ActiveLearningInfo(
        running=stats.running,
        started_ts=stats.started_ts,
        frames_processed=stats.frames_processed,
        auto_labeled=stats.auto_labeled,
        sent_for_review=stats.sent_for_review,
        skipped=stats.skipped,
        errors=stats.errors,
        last_error=stats.last_error,
        review_pending=counts.get("pending", 0),
        review_completed=counts.get("completed", 0),
        label_studio_url=cfg.label_studio_url,
        token_set=bool(cfg.label_studio_token.strip()),
        project_id=cfg.project_id,
        project_name=cfg.project_name,
        labeling_model=cfg.labeling_model,
        finetune_model=cfg.finetune_model,
        source=cfg.source,
        interval_seconds=cfg.interval_seconds,
        confidence_threshold=cfg.confidence_threshold,
        review_empty_frames=cfg.review_empty_frames,
        dataset_id=dataset_id,
        max_review_per_session=cfg.max_review_per_session,
        platform=platform_summary(),
        annotated_samples=annotated,
        dataset_version=version,
        training_ready=annotated >= 1,
    )


@router.get("", response_model=ActiveLearningInfo)
def active_learning_info(ctx: AppContext = Depends(get_context)) -> ActiveLearningInfo:
    """Pipeline status + settings for the Active Learning tab."""
    return _info(ctx)


@router.post("/settings", response_model=ActiveLearningInfo)
def update_settings(
    body: ActiveLearningSettings, ctx: AppContext = Depends(get_context)
) -> ActiveLearningInfo:
    """Update any subset of the active-learning settings (persisted)."""
    cfg = ctx.config.active_learning
    if body.label_studio_url is not None:
        url = body.label_studio_url.strip().rstrip("/")
        if cfg.label_studio_url != url:
            cfg.project_id = 0  # a different server has different projects
        cfg.label_studio_url = url
    if body.label_studio_token is not None:
        cfg.label_studio_token = body.label_studio_token.strip()
    if body.project_id is not None:
        cfg.project_id = max(0, body.project_id)
    if body.project_name is not None and body.project_name.strip():
        cfg.project_name = body.project_name.strip()
    if body.labeling_model is not None:
        cfg.labeling_model = body.labeling_model
    if body.finetune_model is not None:
        if body.finetune_model not in ("yolo", "florence2", "qwen2.5-vl"):
            raise HTTPException(status_code=400, detail="unknown fine-tune model")
        cfg.finetune_model = body.finetune_model
    if body.source is not None:
        cfg.source = body.source
    if body.interval_seconds is not None:
        cfg.interval_seconds = max(1.0, body.interval_seconds)
    if body.confidence_threshold is not None:
        cfg.confidence_threshold = body.confidence_threshold
    if body.review_empty_frames is not None:
        cfg.review_empty_frames = body.review_empty_frames
    if body.dataset_id is not None:
        cfg.dataset_id = max(0, body.dataset_id)
    if body.max_review_per_session is not None:
        cfg.max_review_per_session = max(0, body.max_review_per_session)
    ctx.config.save()
    return _info(ctx)


@router.post("/start", response_model=ActiveLearningInfo)
def start(ctx: AppContext = Depends(get_context)) -> ActiveLearningInfo:
    """Start the watch → pre-label → review loop with the saved settings."""
    try:
        ctx.active_learning.start()
    except LabelStudioError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _info(ctx)


@router.post("/stop", response_model=ActiveLearningInfo)
def stop(ctx: AppContext = Depends(get_context)) -> ActiveLearningInfo:
    ctx.active_learning.stop()
    return _info(ctx)


@router.get("/backends", response_model=list[LabelingBackendInfo])
def labeling_backends(ctx: AppContext = Depends(get_context)) -> list[LabelingBackendInfo]:
    """Models that can watch + pre-label frames, with availability."""
    infos = list_labeling_backends(ctx.store, ctx.detector, ctx.analyzer)
    return [LabelingBackendInfo(**vars(i)) for i in infos]


@router.get("/finetune-backends", response_model=list[FinetuneBackendInfo])
def finetune_backends(ctx: AppContext = Depends(get_context)) -> list[FinetuneBackendInfo]:
    """Fine-tune targets, with what this machine supports (GPU/OS/packages)."""
    infos = list_finetune_backends(ctx.store)
    return [FinetuneBackendInfo(**vars(i)) for i in infos]


@router.post("/labelstudio/test", response_model=LabelStudioStatusInfo)
def test_label_studio(ctx: AppContext = Depends(get_context)) -> LabelStudioStatusInfo:
    """Probe the configured Label Studio server + token."""
    status = ctx.active_learning.label_studio.status()
    return LabelStudioStatusInfo(**vars(status))


@router.get("/labelstudio/projects", response_model=list[LabelStudioProjectInfo])
def label_studio_projects(
    ctx: AppContext = Depends(get_context),
) -> list[LabelStudioProjectInfo]:
    try:
        projects = ctx.active_learning.label_studio.list_projects()
    except LabelStudioError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [LabelStudioProjectInfo(**p) for p in projects]


@router.post("/sync", response_model=ActiveLearningSyncResult)
def sync_annotations(ctx: AppContext = Depends(get_context)) -> ActiveLearningSyncResult:
    """Pull completed Label Studio annotations back onto their samples."""
    try:
        result = ctx.active_learning.sync()
    except LabelStudioError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ActiveLearningSyncResult(**result)


@router.post("/train", response_model=TrainingRunInfo)
def start_finetune(
    body: ActiveLearningTrainRequest, ctx: AppContext = Depends(get_context)
) -> TrainingRunInfo:
    """Fine-tune the configured target model on the accumulated dataset."""
    try:
        run = ctx.active_learning.train(epochs=body.epochs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    from tailcam.web.routes_api import _run_info

    return _run_info(run)


@router.post("/runs/{run_id}/stop", response_model=OkResponse)
def stop_finetune(run_id: int, ctx: AppContext = Depends(get_context)) -> OkResponse:
    """Stop a Florence-2/Qwen fine-tune run (YOLO runs stop via /api/training)."""
    if not ctx.active_learning.stop_run(run_id) and not ctx.training.stop_run(run_id):
        raise HTTPException(status_code=404, detail="run not found or already finished")
    return OkResponse(detail="stopping")
