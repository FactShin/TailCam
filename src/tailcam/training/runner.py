"""Training execution: build a YOLO classification dataset from labeled samples
and fine-tune a model on the GPU.

The Ultralytics call is isolated in :func:`train_model` so it can be stubbed in
tests (real training needs torch + a GPU, which CI doesn't have). Everything
else — the train/val export, class selection, metric capture — is plain logic.
"""

from __future__ import annotations

import random
import shutil
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from tailcam.logging_setup import get_logger
from tailcam.persistence.store import Store

log = get_logger(__name__)


def export_classification_dataset(
    store: Store,
    dataset_id: int,
    out_dir: Path,
    val_frac: float = 0.2,
    min_per_class: int = 2,
) -> tuple[list[str], int, int]:
    """Lay out labeled samples as ``out_dir/{train,val}/<class>/*.jpg`` (the
    format Ultralytics classification expects). Returns (classes, n_train, n_val).
    Raises ValueError if there isn't enough labeled data to train."""
    by_class: dict[str, list[str]] = defaultdict(list)
    for s in store.list_samples(dataset_id, limit=1_000_000):
        if s.label and Path(s.path).exists():
            by_class[s.label].append(s.path)

    classes = sorted(c for c, items in by_class.items() if len(items) >= min_per_class)
    if len(classes) < 2:
        raise ValueError(
            "need at least 2 labeled classes with "
            f"{min_per_class}+ samples each (have: "
            + ", ".join(f"{c}={len(by_class[c])}" for c in sorted(by_class)) + ")"
        )

    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    n_train = n_val = 0
    rng = random.Random(1234)  # deterministic split
    for cls in classes:
        items = list(by_class[cls])
        rng.shuffle(items)
        n_val_c = max(1, int(len(items) * val_frac))
        splits = {"val": items[:n_val_c], "train": items[n_val_c:]}
        if not splits["train"]:  # tiny class — keep at least one in train
            splits["train"] = splits["val"][:1]
            splits["val"] = splits["val"][1:] or splits["train"]
        for split, group in splits.items():
            dest = out_dir / split / cls
            dest.mkdir(parents=True, exist_ok=True)
            for i, src in enumerate(group):
                try:
                    shutil.copyfile(src, dest / f"{i:06d}.jpg")
                except OSError:  # pragma: no cover
                    continue
            if split == "train":
                n_train += len(group)
            else:
                n_val += len(group)
    return classes, n_train, n_val


def train_model(
    base_model: str,
    data_dir: Path,
    epochs: int,
    imgsz: int,
    device: str,
    project_dir: Path,
    on_epoch: Callable[[int], None],
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Fine-tune a YOLO classifier. Returns {'model_path', 'metrics'}.

    Isolated for testability — monkeypatched in tests. Real runs import
    Ultralytics lazily so the dependency stays optional.
    """
    from ultralytics import YOLO

    model = YOLO(base_model)

    def _cb(trainer) -> None:  # pragma: no cover - exercised only with real torch
        try:
            on_epoch(int(getattr(trainer, "epoch", 0)) + 1)
            if should_stop is not None and should_stop():
                trainer.stop = True
        except Exception:
            pass

    model.add_callback("on_train_epoch_end", _cb)
    model.train(
        data=str(data_dir),
        epochs=epochs,
        imgsz=imgsz,
        device=None if device in ("cpu", "none") else device,
        project=str(project_dir),
        name="train",
        exist_ok=True,
        verbose=False,
    )
    best = project_dir / "train" / "weights" / "best.pt"
    metrics: dict = {}
    try:  # pragma: no cover - depends on real training output
        results = getattr(model, "metrics", None)
        top1 = getattr(getattr(results, "top1", None), "item", lambda: None)()
        if top1 is not None:
            metrics["top1"] = round(float(top1), 4)
    except Exception:
        pass
    return {"model_path": str(best), "metrics": metrics}
