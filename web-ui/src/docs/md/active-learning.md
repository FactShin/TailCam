# Active learning (human-in-the-loop)

Active learning turns model training from "label everything" into "label only
what the model isn't sure about." A **labeling model** watches frames from your
cameras (or a dataset), keeps its **confident** detections as machine labels,
and sends only the **uncertain** frames to **Label Studio** for you to review.
Your corrections sync back into the dataset, which fine-tunes the model of your
choice — and the improved model can watch the next round. Every loop makes your
homemade model better.

It all lives in **AI Studio → Active Learning**.

## The workflow

1. **Pick the watcher** — the model that monitors + pre-labels frames.
2. **Pick the source** — all cameras, one camera, or an existing dataset.
3. **Set the confidence threshold.** Detections at/above it are auto-labeled;
   frames with anything below it go to Label Studio.
4. **Connect Label Studio** (URL + API token; TailCam creates the project).
5. Click **Start Active Learning**.
6. **Label the uncertain frames** in Label Studio — as many boxes per image as
   you need, starting from the model's own pre-annotations.
7. **Sync** the completed annotations back.
8. **Fine-tune** the target model on the accumulated dataset.
9. Activate the new model (Models → Your models) — or make it the watcher.

No files are moved by hand at any point.

## Labeling (watcher) models

| Model | Boxes | Runs on | Notes |
| --- | --- | --- | --- |
| Built-in detector | yes | everywhere | Zero setup, 80 COCO classes. |
| Your trained / BYO detection model | yes | everywhere (with the training engine) | Pick any registry model with `task=detection`. |
| Ollama | no (whole frame) | wherever Ollama runs | Classification only — reviews start from a full-frame region. |
| Florence-2 | yes | CUDA / Apple MPS / CPU | Open-vocabulary detection; `pip install 'tailcam[florence2]'`. |
| Qwen2.5-VL | yes | CUDA / Apple MPS / CPU | Grounded detection via JSON; `pip install 'tailcam[qwen-vl]'`. |

Unavailable models say exactly what's missing (package, server, GPU) in the
selector — nothing hard-fails.

## Fine-tune targets

- **TailCam YOLO (Ultralytics)** — the default; the same pipeline as the
  Training tab (`pip install 'tailcam[training]'`). Works on Linux and macOS
  (CUDA, Apple MPS, or CPU).
- **Florence-2** — a simple full-precision fine-tune loop over the Florence OD
  format. Practical on CUDA; works on Apple MPS/CPU but slowly. Experimental:
  batch size 1, no eval split — treat it as a starting point.
- **Qwen2.5-VL via Unsloth** — QLoRA fine-tuning. **Requires an NVIDIA CUDA
  GPU** (`pip install unsloth` on Linux/WSL). On macOS the UI clearly reports
  inference-only support instead of failing — fine-tune on a Linux CUDA box and
  copy the adapter directory across if needed.

Fine-tuned VLMs are registered under **Models → Your models** (kind `trained`,
base `florence2` / `qwen2.5-vl`). They're used by the active-learning watcher;
the live motion pipeline keeps using YOLO models.

## Setting up Label Studio

Label Studio is a separate server. On Linux **and** macOS:

```bash
pip install label-studio        # ideally in its own venv/pipx
label-studio start              # serves http://localhost:8080
```

Then in TailCam:

1. `pip install 'tailcam[activelearning]'` (the Label Studio Python SDK).
2. AI Studio → Active Learning → **Label Studio** panel: enter the URL and the
   **API token** from Label Studio's *Account & Settings → Access Token*.
3. **Save & test** — the badge flips to *connected*.
4. Leave the project on *Create automatically*, or pick an existing project.

The token is stored in `config.toml` (`[active_learning]`) next to TailCam's
other integration secrets, is never logged, and is never echoed back by the
API. Frames are imported **inline** (base64), so Label Studio needs no storage
configuration and doesn't need to share a filesystem with TailCam — it can run
on another machine on your tailnet.

Config keys (`[active_learning]`):

| Setting | Default | Meaning |
| --- | --- | --- |
| `label_studio_url` | `http://localhost:8080` | The Label Studio server. |
| `label_studio_token` | — | API token (write-only via the UI). |
| `project_id` | 0 | 0 = find/create by `project_name`. |
| `project_name` | `TailCam Active Learning` | Auto-created project title. |
| `labeling_model` | `builtin` | `builtin`, `ollama`, `florence2`, `qwen2.5-vl`, or `model:<id>`. |
| `finetune_model` | `yolo` | `yolo`, `florence2`, or `qwen2.5-vl`. |
| `source` | `cameras` | `cameras`, `camera:<id>`, or `dataset:<id>`. |
| `interval_seconds` | 10 | Seconds between frame batches. |
| `confidence_threshold` | 0.60 | The auto-label / review split. |
| `review_empty_frames` | false | Also review frames where the model saw nothing. |
| `dataset_id` | 0 | Target dataset (0 = auto-create "Active learning"). |
| `max_review_per_session` | 200 | Cap on Label Studio submissions per session. |

## How the confidence threshold works

Every detection carries a confidence (0–1). For each frame:

- **All detections ≥ threshold** → the frame is saved as a machine-labeled
  sample with its boxes (`source: active-auto`).
- **Any detection < threshold** → the frame goes to Label Studio with the
  model's boxes attached as pre-annotations (`source: active-review`).
- **No detections** → skipped, unless *review empty frames* is on.

Lower thresholds auto-label more (faster dataset, more label noise); higher
thresholds review more (more of your time, cleaner labels). 60% is a sane
start. Note Florence-2 and Qwen2.5-VL don't emit calibrated per-box scores, so
their boxes use fixed confidences (0.75 / 0.70).

## Labeling multiple objects in one image

The auto-created project uses a `RectangleLabels` config — draw as many boxes
per image as you like, each with its own class. TailCam converts every region
back to its normalized annotation format on sync. If you need classification,
keypoints, or segmentation too, extend the label config in Label Studio's
project settings; non-rectangle regions are ignored by the sync (they don't map
to boxes) but don't break it.

## Syncing and dataset versions

**Sync annotations** pulls every completed Label Studio task, writes the boxes
onto the originating sample, marks it human-labeled, and bumps the **dataset
version** — so any trained model can be traced to the dataset state it saw.
Sync is idempotent: run it whenever you finish labeling.

Everything is tracked per sample: which model pre-labeled it, its confidence,
timestamp, camera source, and whether the label is machine (`active-auto`) or
human-reviewed (`active-review` + completed review item).

## Platform notes

**Linux** — everything works; Unsloth/Qwen fine-tuning needs an NVIDIA GPU with
CUDA. **macOS** — the loop, Label Studio, YOLO and Florence-2 paths work
(Apple-silicon GPUs are used via MPS where possible); Qwen fine-tuning via
Unsloth is CUDA-only and the UI says so instead of failing. Windows follows the
Linux notes (CUDA for Unsloth via WSL).

## Troubleshooting

- **"cannot reach Label Studio"** — the server isn't running or the URL/port is
  wrong. `label-studio start`, then Save & test. Across machines, use the
  host's tailnet address, not `localhost`.
- **"API token rejected"** — copy a fresh token from Label Studio *Account &
  Settings → Access Token* and save it again.
- **"project #N not found"** — the project was deleted or belongs to a
  different server. Set the project back to *Create automatically*.
- **Images not visible in Label Studio** — TailCam imports frames inline, so
  this normally can't happen; if tasks look empty, check the Label Studio
  version (needs data-URI image support, any recent release) and re-run a
  session.
- **Model failed to load** — the selector's detail text names the missing
  package or file; VLMs also need their first-run download (several GB) to
  finish.
- **GPU unavailable** — Florence-2/YOLO fall back to MPS/CPU (slow but
  working); Qwen/Unsloth fine-tuning refuses with a clear message.
- **"training format conversion failed" / no annotated samples** — sync first;
  fine-tuning needs at least one sample with boxes.
- **Review cap reached** — raise `max_review_per_session` (or 0 = unlimited).

## API

Everything the tab does is REST under `/api/active-learning`: `GET` (status),
`POST /settings`, `/start`, `/stop`, `/sync`, `/train`,
`/labelstudio/test`, `GET /labelstudio/projects`, `GET /backends`,
`GET /finetune-backends`.
