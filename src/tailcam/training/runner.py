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


def export_detection_dataset(
    store: Store,
    dataset_id: int,
    out_dir: Path,
    val_frac: float = 0.2,
    min_boxes: int = 1,
) -> tuple[list[str], int, int]:
    """Lay out annotated samples as a YOLO *detection* dataset::

        out_dir/images/{train,val}/*.jpg
        out_dir/labels/{train,val}/*.txt   # "<class_idx> cx cy w h" per box
        out_dir/data.yaml

    Only samples with at least ``min_boxes`` boxes are exported. Returns
    (classes, n_train, n_val); raises ValueError if there's nothing to train on.
    """
    annotated: list[tuple[str, list]] = []  # (image_path, [SampleAnnotationRecord])
    label_set: set[str] = set()
    for s in store.list_samples(dataset_id, limit=1_000_000):
        if not Path(s.path).exists() or s.id is None:
            continue
        boxes = store.list_annotations(s.id)
        if len(boxes) < min_boxes:
            continue
        annotated.append((s.path, boxes))
        label_set.update(b.label for b in boxes)

    classes = sorted(label_set)
    if not classes or not annotated:
        raise ValueError(
            "need at least one labeled box; annotate samples before training "
            f"(annotated samples: {len(annotated)}, classes: {len(classes)})"
        )
    class_idx = {name: i for i, name in enumerate(classes)}

    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    rng = random.Random(1234)  # deterministic split
    rng.shuffle(annotated)
    n_val = max(1, int(len(annotated) * val_frac)) if len(annotated) > 1 else 0
    splits = {"val": annotated[:n_val], "train": annotated[n_val:]}
    if not splits["train"]:  # tiny dataset — keep everything trainable
        splits["train"] = annotated
        splits["val"] = annotated[:1]

    counts = {"train": 0, "val": 0}
    for split, items in splits.items():
        for i, (src, boxes) in enumerate(items):
            stem = f"{i:06d}"
            try:
                shutil.copyfile(src, out_dir / "images" / split / f"{stem}.jpg")
            except OSError:  # pragma: no cover
                continue
            lines = [
                f"{class_idx[b.label]} {b.cx:.6f} {b.cy:.6f} {b.w:.6f} {b.h:.6f}"
                for b in boxes
                if b.label in class_idx
            ]
            (out_dir / "labels" / split / f"{stem}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else "")
            )
            counts[split] += 1

    names = "\n".join(f"  {i}: {name}" for i, name in enumerate(classes))
    (out_dir / "data.yaml").write_text(
        f"path: {out_dir}\ntrain: images/train\nval: images/val\nnames:\n{names}\n"
    )
    return classes, counts["train"], counts["val"]


def train_model(
    base_model: str,
    data_dir: Path,
    epochs: int,
    imgsz: int,
    device: str,
    project_dir: Path,
    on_epoch: Callable[[int], None],
    should_stop: Callable[[], bool] | None = None,
    task: str = "classification",
) -> dict:
    """Fine-tune a YOLO model. Returns {'model_path', 'metrics'}.

    ``task`` selects classification (``data_dir`` is a folder tree) vs detection
    (``data_dir`` holds a ``data.yaml``). Isolated for testability —
    monkeypatched in tests. Real runs import Ultralytics lazily.
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
    data = str(data_dir / "data.yaml") if task == "detection" else str(data_dir)
    model.train(
        data=data,
        epochs=epochs,
        imgsz=imgsz,
        device=None if device in ("cpu", "none") else device,
        project=str(project_dir),
        name="train",
        exist_ok=True,
        verbose=False,
    )
    best = project_dir / "train" / "weights" / "best.pt"
    return {"model_path": str(best), "metrics": _extract_metrics(model, task)}


def _extract_metrics(model, task: str) -> dict:  # pragma: no cover - needs real torch
    """Pull the headline accuracy metric from a finished run (top1 for
    classification, mAP50 for detection)."""
    metrics: dict = {}
    results = getattr(model, "metrics", None)
    try:
        if task == "detection":
            box = getattr(results, "box", None)
            map50 = getattr(box, "map50", None)
            if map50 is not None:
                metrics["map50"] = round(float(map50), 4)
        else:
            top1 = getattr(getattr(results, "top1", None), "item", lambda: None)()
            if top1 is not None:
                metrics["top1"] = round(float(top1), 4)
    except Exception:
        pass
    return metrics
