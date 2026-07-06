"""Canonical annotation format + converters (Label Studio ⇄ TailCam ⇄ models).

TailCam's internal region format is the one the annotation editor and YOLO
export already use: normalized center/size boxes ``(cx, cy, w, h)`` in 0..1.
Everything converts through :class:`FrameAnnotation` / :class:`AnnotatedFrame`
so adding a new annotation source or training format only means one converter.

Label Studio rectangles are percent-based top-left/size (``x, y, width,
height`` in 0..100), so the conversions here are pure arithmetic — no image
decoding required.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

# Annotation provenance values (who produced the label).
SOURCE_MACHINE = "machine"
SOURCE_HUMAN = "human"
SOURCE_REVIEWED = "reviewed-machine"


@dataclass
class FrameAnnotation:
    """One labeled region. Coordinates are normalized 0..1 center/size — the
    same layout as SampleAnnotationRecord, so store writes are direct."""

    label: str
    cx: float
    cy: float
    w: float
    h: float
    confidence: float | None = None
    source: str = SOURCE_MACHINE  # machine | human | reviewed-machine


@dataclass
class AnnotatedFrame:
    """A frame plus everything the pipeline knows about it — the canonical
    interchange record between capture, Label Studio, and training export."""

    image_path: str
    annotations: list[FrameAnnotation] = field(default_factory=list)
    camera_id: str = ""  # camera source ("" for dataset-file frames)
    source_video: str = ""  # originating video/recording path, if any
    timestamp: float = 0.0
    frame_number: int = 0
    labeling_model: str = ""  # backend id that pre-labeled the frame
    dataset_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def clamp01(value: object) -> float:
    try:
        return min(1.0, max(0.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# -- Label Studio ------------------------------------------------------------

# Value keys of a Label Studio rectangle result (percent of image size).
_LS_FROM_NAME = "label"
_LS_TO_NAME = "image"


def label_config_xml(labels: list[str]) -> str:
    """The default object-detection labeling config: multiple rectangles per
    image, one class per rectangle. Extend by editing the project in Label
    Studio (classification/keypoints/segmentation tags can be added alongside)."""
    label_tags = "\n".join(
        f'    <Label value="{_xml_escape(lb)}"/>' for lb in labels if lb.strip()
    )
    return (
        "<View>\n"
        f'  <Image name="{_LS_TO_NAME}" value="$image" zoom="true"/>\n'
        f'  <RectangleLabels name="{_LS_FROM_NAME}" toName="{_LS_TO_NAME}">\n'
        f"{label_tags}\n"
        "  </RectangleLabels>\n"
        "</View>"
    )


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def to_label_studio_predictions(
    annotations: list[FrameAnnotation], model_id: str
) -> dict:
    """Build the ``predictions`` entry for a Label Studio task so the reviewer
    starts from the model's boxes instead of a blank image."""
    results = []
    for i, a in enumerate(annotations):
        x = clamp01(a.cx - a.w / 2) * 100.0
        y = clamp01(a.cy - a.h / 2) * 100.0
        results.append(
            {
                "id": f"pred-{i}",
                "type": "rectanglelabels",
                "from_name": _LS_FROM_NAME,
                "to_name": _LS_TO_NAME,
                "original_width": 100,
                "original_height": 100,
                "value": {
                    "x": x,
                    "y": y,
                    "width": min(a.w * 100.0, 100.0 - x),
                    "height": min(a.h * 100.0, 100.0 - y),
                    "rotation": 0,
                    "rectanglelabels": [a.label],
                },
                "score": a.confidence,
            }
        )
    scores = [a.confidence for a in annotations if a.confidence is not None]
    return {
        "model_version": model_id,
        "score": min(scores) if scores else None,
        "result": results,
    }


def to_label_studio_task(frame: AnnotatedFrame, image_data: str, model_id: str) -> dict:
    """One importable Label Studio task. ``image_data`` is what LS displays —
    a data URI (uploaded inline, works with zero storage setup) or a served URL."""
    task: dict = {
        "data": {
            "image": image_data,
            "meta": {
                "tailcam_image_path": frame.image_path,
                "camera_id": frame.camera_id,
                "source_video": frame.source_video,
                "timestamp": frame.timestamp,
                "frame_number": frame.frame_number,
                "labeling_model": frame.labeling_model,
            },
        }
    }
    if frame.annotations:
        task["predictions"] = [to_label_studio_predictions(frame.annotations, model_id)]
    return task


def from_label_studio_result(result: list[dict]) -> list[FrameAnnotation]:
    """Convert one completed Label Studio annotation's ``result`` list into
    canonical boxes. Non-rectangle regions (relations, classifications added by
    a customized config) are skipped — they don't map to boxes."""
    boxes: list[FrameAnnotation] = []
    for item in result or []:
        if item.get("type") != "rectanglelabels":
            continue
        value = item.get("value") or {}
        labels = value.get("rectanglelabels") or []
        if not labels:
            continue
        w = clamp01(float(value.get("width", 0)) / 100.0)
        h = clamp01(float(value.get("height", 0)) / 100.0)
        if w <= 0 or h <= 0:
            continue
        x = clamp01(float(value.get("x", 0)) / 100.0)
        y = clamp01(float(value.get("y", 0)) / 100.0)
        boxes.append(
            FrameAnnotation(
                label=str(labels[0]),
                cx=clamp01(x + w / 2),
                cy=clamp01(y + h / 2),
                w=w,
                h=h,
                confidence=None,
                source=SOURCE_HUMAN,
            )
        )
    return boxes


# -- model training formats ---------------------------------------------------


def to_yolo_lines(annotations: list[FrameAnnotation], class_idx: dict[str, int]) -> list[str]:
    """YOLO detection label lines: ``<class> cx cy w h`` (normalized)."""
    return [
        f"{class_idx[a.label]} {a.cx:.6f} {a.cy:.6f} {a.w:.6f} {a.h:.6f}"
        for a in annotations
        if a.label in class_idx
    ]


def to_florence_od_string(
    annotations: list[FrameAnnotation], width: int = 1000, height: int = 1000
) -> str:
    """Florence-2 OD target string: ``label<loc_x1><loc_y1><loc_x2><loc_y2>…``
    with coordinates binned to 0..999 (the model's location vocabulary)."""
    parts: list[str] = []
    for a in annotations:
        x1 = int(clamp01(a.cx - a.w / 2) * (width - 1))
        y1 = int(clamp01(a.cy - a.h / 2) * (height - 1))
        x2 = int(clamp01(a.cx + a.w / 2) * (width - 1))
        y2 = int(clamp01(a.cy + a.h / 2) * (height - 1))
        parts.append(f"{a.label}<loc_{x1}><loc_{y1}><loc_{x2}><loc_{y2}>")
    return "".join(parts)


def to_qwen_json(annotations: list[FrameAnnotation], width: int, height: int) -> str:
    """Qwen2.5-VL grounding target: a JSON list of absolute-pixel boxes, the
    format its detection prompts are trained to emit."""
    objects = [
        {
            "bbox_2d": [
                int(clamp01(a.cx - a.w / 2) * width),
                int(clamp01(a.cy - a.h / 2) * height),
                int(clamp01(a.cx + a.w / 2) * width),
                int(clamp01(a.cy + a.h / 2) * height),
            ],
            "label": a.label,
        }
        for a in annotations
    ]
    return json.dumps(objects)


def now_ts() -> float:
    return time.time()
