"""Built-in plug-and-play object detector: boxes + labels with zero setup.

The promise: flip one switch (it's even on by default) and the camera view
shows live bounding boxes labeled person / cup / bottle / cat / dog / … (the
80 COCO classes). No accounts, no cloud, no model shopping — the first time a
detection is requested, the model downloads itself in the background and
detection starts as soon as it lands.

Engine ladder (``detection.engine = "auto"``):

1. **Ultralytics YOLO11n** — used when the optional training extra (torch) is
   installed. Best accuracy, GPU-capable. The weights (~5 MB) come from the
   official Ultralytics release assets.
2. **OpenCV DNN + YOLOv4-tiny** — needs nothing beyond TailCam's own
   dependencies (opencv-python-headless ships the DNN module). Weights
   (~23 MB) come from the official darknet release. This is the path a
   lay user gets, and it just works on CPU.

Enthusiasts can pin ``engine``, override ``model`` with any Ultralytics detect
model (e.g. ``yolo11s.pt`` or a custom path), tune ``confidence``, or filter
``classes``. Trained/BYO detection models (AI Studio) still take priority over
this built-in — see ``InferenceRouter.detect``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from tailcam import paths
from tailcam.ai.analyzer import Detection
from tailcam.config import DetectionConfig
from tailcam.logging_setup import get_logger

if TYPE_CHECKING:  # pragma: no cover
    pass

log = get_logger(__name__)

# The 80 COCO class names, in model output order (both engines use COCO).
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon",
    "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

# Download sources, tried in order: the official release first, then a mirror
# (some networks/proxies block GitHub release downloads). Every downloaded
# model must pass load-time validation before it is ever used.
_ULTRALYTICS_ASSETS = "https://github.com/ultralytics/assets/releases/download/v8.3.0/"
_DEFAULT_ULTRALYTICS_MODEL = "yolo11n.pt"
_HF_DARKNET = "https://huggingface.co/homohapiens/darknet-yolov4/resolve/main/"
_YOLO4_CFG_URLS = [
    "https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg",
    _HF_DARKNET + "yolov4-tiny.cfg",
]
_YOLO4_WEIGHTS_URLS = [
    "https://github.com/AlexeyAB/darknet/releases/download/"
    "darknet_yolo_v4_pre/yolov4-tiny.weights",
    _HF_DARKNET + "yolov4-tiny.weights",
]
_YOLO4_WEIGHTS_MIN_BYTES = 20_000_000  # sanity floor; real file is ~23.6 MB

_DOWNLOAD_TIMEOUT = 600.0
_RETRY_BACKOFF_S = 60.0


def builtin_models_dir() -> Path:
    return paths.models_dir() / "builtin"


@dataclass
class DetectorStatus:
    """Snapshot for the API/UI: what the built-in detector is doing right now."""

    enabled: bool = False
    engine: str = ""  # ultralytics | opencv | ""
    model: str = ""
    status: str = "off"  # off | idle | downloading | ready | error
    percent: float = 0.0
    detail: str = ""
    error: str = ""
    classes: int = len(COCO_CLASSES)


class BuiltinDetector:
    """Lazy, self-provisioning COCO detector shared by all cameras.

    ``detect()`` never blocks on a download: the first call kicks a background
    fetch and returns ``[]`` until the engine is ready. All engine inference is
    serialized behind a lock (cv2.dnn nets are not thread-safe).
    """

    def __init__(self, config: DetectionConfig) -> None:
        self._config = config
        self._lock = threading.Lock()  # guards state below + inference
        self._download_thread: threading.Thread | None = None
        self._last_attempt = 0.0  # monotonic; throttles retry after an error
        self._engine: str = ""
        self._model_name: str = ""
        self._status: str = "idle"
        self._percent: float = 0.0
        self._detail: str = ""
        self._error: str = ""
        # Engine handles (exactly one is set once ready).
        self._yolo: Any = None  # ultralytics.YOLO
        self._net_model: Any = None  # cv2.dnn_DetectionModel

    # -- engine selection ----------------------------------------------------

    def _ultralytics_available(self) -> bool:
        import importlib.util

        return importlib.util.find_spec("ultralytics") is not None

    def _resolve_engine(self) -> str:
        wanted = (self._config.engine or "auto").strip().lower()
        if wanted == "ultralytics":
            return "ultralytics"
        if wanted == "opencv":
            return "opencv"
        return "ultralytics" if self._ultralytics_available() else "opencv"

    # -- public surface -------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._status == "ready"

    def status(self) -> DetectorStatus:
        with self._lock:
            if not self._config.enabled:
                return DetectorStatus(enabled=False, status="off")
            return DetectorStatus(
                enabled=True,
                engine=self._engine or self._resolve_engine(),
                model=self._model_name or self._default_model_name(),
                status=self._status,
                percent=self._percent,
                detail=self._detail,
                error=self._error,
            )

    def _default_model_name(self) -> str:
        if self._resolve_engine() == "ultralytics":
            return (self._config.model or _DEFAULT_ULTRALYTICS_MODEL).strip()
        return "yolov4-tiny"

    def ensure_ready(self) -> None:
        """Start provisioning (download + load) in the background if needed."""
        if not self._config.enabled:
            return
        with self._lock:
            if self._status in ("ready", "downloading"):
                return
            if self._download_thread is not None and self._download_thread.is_alive():
                return
            # After a failure (offline, blocked URL), retry at most once a
            # minute — detect() is polled every ~1.5s per open viewer.
            now = time.monotonic()
            if self._status == "error" and now - self._last_attempt < _RETRY_BACKOFF_S:
                return
            self._last_attempt = now
            self._status = "downloading"
            self._percent = 0.0
            self._error = ""
            self._detail = "preparing model"
            self._download_thread = threading.Thread(
                target=self._provision, name="detector-provision", daemon=True
            )
            self._download_thread.start()

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Boxes for one frame. `[]` while disabled, downloading, or on error."""
        if not self._config.enabled:
            return []
        self.ensure_ready()
        with self._lock:
            if self._status != "ready":
                return []
            try:
                if self._yolo is not None:
                    detections = self._detect_ultralytics(image)
                elif self._net_model is not None:
                    detections = self._detect_opencv(image)
                else:  # pragma: no cover - ready implies an engine handle
                    return []
            except Exception as exc:  # pragma: no cover - driver/tensor edge
                log.warning("builtin detection failed: %s", exc)
                return []
        allowed = {c.strip().lower() for c in self._config.classes if c.strip()}
        if allowed:
            detections = [d for d in detections if d.label.lower() in allowed]
        return detections

    # -- provisioning ----------------------------------------------------------

    def _set_state(self, **kw: Any) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, f"_{k}", v)

    def _provision(self) -> None:
        try:
            engine = self._resolve_engine()
            if engine == "ultralytics":
                self._provision_ultralytics()
            else:
                self._provision_opencv()
        except Exception as exc:
            log.warning("builtin detector provisioning failed: %s", exc)
            self._set_state(status="error", error=str(exc), detail="")

    def _download(self, urls: list[str], dest: Path, label: str) -> None:
        """Stream a file to ``dest`` (atomic via .part), updating progress.

        ``urls`` are alternate sources for the SAME file, tried in order —
        GitHub release downloads are blocked on some networks, so a mirror
        keeps "plug and play" true there too.
        """
        import httpx

        if dest.exists() and dest.stat().st_size > 0:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        last_exc: Exception | None = None
        for url in urls:
            self._set_state(detail=f"downloading {label}", percent=0.0)
            try:
                with httpx.Client(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client, \
                        client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length") or 0)
                    done = 0
                    with part.open("wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=1 << 16):
                            fh.write(chunk)
                            done += len(chunk)
                            if total:
                                self._set_state(percent=round(100.0 * done / total, 1))
                part.replace(dest)
                return
            except Exception as exc:
                last_exc = exc
                log.info("download of %s from %s failed (%s); trying next source", label, url, exc)
                part.unlink(missing_ok=True)
        raise RuntimeError(f"could not download {label}: {last_exc}")

    def _provision_ultralytics(self) -> None:
        model_ref = (self._config.model or _DEFAULT_ULTRALYTICS_MODEL).strip()
        # A bare asset name (yolo11n.pt) is downloaded to our models dir from
        # the official release; an explicit path/custom name is passed through
        # for Ultralytics to resolve.
        if "/" not in model_ref and "\\" not in model_ref and model_ref.endswith(".pt"):
            local = builtin_models_dir() / model_ref
            if not local.exists():
                self._download([_ULTRALYTICS_ASSETS + model_ref], local, model_ref)
            model_ref = str(local)
        self._set_state(detail="loading model")
        from ultralytics import YOLO

        yolo = YOLO(model_ref)
        self._set_state(
            yolo=yolo, engine="ultralytics",
            model_name=Path(model_ref).name, status="ready", percent=100.0, detail="",
        )
        log.info("builtin detector ready (ultralytics, %s)", Path(model_ref).name)

    def _provision_opencv(self) -> None:
        import cv2

        cfg = builtin_models_dir() / "yolov4-tiny.cfg"
        weights = builtin_models_dir() / "yolov4-tiny.weights"
        self._download(_YOLO4_CFG_URLS, cfg, "yolov4-tiny.cfg")
        self._download(_YOLO4_WEIGHTS_URLS, weights, "yolov4-tiny.weights (23 MB)")
        if weights.stat().st_size < _YOLO4_WEIGHTS_MIN_BYTES:
            weights.unlink(missing_ok=True)
            raise RuntimeError("model download was truncated — will retry")
        self._set_state(detail="loading model")
        # Loading doubles as integrity validation: a corrupt file fails here,
        # so delete it and let the next attempt re-download.
        try:
            net = cv2.dnn.readNetFromDarknet(str(cfg), str(weights))
        except cv2.error as exc:
            weights.unlink(missing_ok=True)
            cfg.unlink(missing_ok=True)
            raise RuntimeError(f"downloaded model failed to load: {exc}") from exc
        model = cv2.dnn.DetectionModel(net)
        model.setInputParams(size=(416, 416), scale=1.0 / 255.0, swapRB=True)
        self._set_state(
            net_model=model, engine="opencv",
            model_name="yolov4-tiny", status="ready", percent=100.0, detail="",
        )
        log.info("builtin detector ready (opencv-dnn, yolov4-tiny)")

    # -- inference (caller holds the lock) --------------------------------------

    def _detect_ultralytics(self, image: np.ndarray) -> list[Detection]:
        result = self._yolo.predict(
            image, verbose=False, conf=max(0.05, float(self._config.confidence))
        )[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return []
        names = getattr(result, "names", {}) or {}
        out: list[Detection] = []
        for (cx, cy, w, h), conf, cls in zip(
            boxes.xywhn.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), strict=False
        ):
            idx = int(cls)
            out.append(
                Detection(
                    label=str(names.get(idx, idx)), confidence=float(conf),
                    cx=float(cx), cy=float(cy), w=float(w), h=float(h),
                )
            )
        return out

    def _detect_opencv(self, image: np.ndarray) -> list[Detection]:
        conf_th = max(0.05, float(self._config.confidence))
        class_ids, confidences, boxes = self._net_model.detect(
            image, confThreshold=conf_th, nmsThreshold=0.4
        )
        ih, iw = image.shape[:2]
        out: list[Detection] = []
        for cls, conf, (x, y, w, h) in zip(
            np.array(class_ids).flatten().tolist(),
            np.array(confidences).flatten().tolist(),
            list(boxes),
            strict=False,
        ):
            idx = int(cls)
            label = COCO_CLASSES[idx] if 0 <= idx < len(COCO_CLASSES) else str(idx)
            out.append(
                Detection(
                    label=label, confidence=float(conf),
                    cx=float((x + w / 2.0) / max(1, iw)),
                    cy=float((y + h / 2.0) / max(1, ih)),
                    w=float(w / max(1, iw)),
                    h=float(h / max(1, ih)),
                )
            )
        return out
