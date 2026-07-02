# Training & models

TailCam can train **your own** models from **your own** footage — both image
**classification** (what is this?) and object **detection** (what + where?). It
uses the Ultralytics/YOLO engine, which is auto-detected. Everything lives in
**AI Studio**: the **Training** tab walks the workflow below, and finished
models appear under **Models → Your models** where you activate them.

This is optional and benefits from a GPU, but small classification models train
fine on CPU.

## The workflow

1. **Collect** frames into a **dataset**.
2. **Label** (and for detection, **annotate** boxes on) the samples.
3. **Train** a run that fine-tunes a base model on the dataset.
4. **Activate** the resulting model so it takes over motion analysis.

## Datasets

Create a dataset for a task:

- `classification` — each sample has a single label.
- `detection` — each sample carries bounding boxes (label + position).

API: `POST /api/datasets` (`name`, `note`, `task`). MCP: `create_dataset`.

Populate it two ways:

- **Import existing events.** `POST /api/datasets/<id>/import-events` (MCP:
  `import_events_to_dataset`) turns your recorded motion-event snapshots into
  labeled samples.
- **Continuous collection.** Sample a frame from every online camera on an
  interval and add it to the active dataset.

### Continuous collection

Configure under `[training]` (or via `POST /api/training/collection`, MCP:
`set_training_collection`):

| Setting | Default | Meaning |
| --- | --- | --- |
| `collect_enabled` | false | Turn collection on. |
| `collect_interval_seconds` | 30.0 | Seconds between samples per camera. |
| `auto_label` | true | Weak-label new samples with the Ollama model. |
| `active_dataset_id` | 0 | Which dataset collected frames go to. |

## Samples and annotations

- List samples: `GET /api/datasets/<id>/samples` (MCP: `list_dataset_samples`).
- Relabel: `PATCH /api/samples/<id>` (MCP: `relabel_sample`).
- Delete: `DELETE /api/samples/<id>` (MCP: `delete_sample`).
- For detection, edit bounding boxes with the annotation editor
  (`PUT /api/samples/<id>/annotations`). Boxes are normalized `cx, cy, w, h` in
  0–1 with a label.

## Training runs

Start a run on a dataset:

```
POST /api/training/runs   { "dataset_id": <id>, "base_model": ..., "epochs": ..., "image_size": ... }
```

MCP: `start_training_run` (requires `confirm=true` — it's resource-heavy). Defaults
come from `[training]`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `base_model` | yolo11n-cls.pt | Classification base (downloaded on first train). |
| `epochs` | 30 | Training epochs. |
| `image_size` | 224 | Classification input size. |
| `detect_base_model` | yolo11n.pt | Detection base. |
| `detect_image_size` | 640 | Detection input size. |
| `detect_conf` | 0.35 | Min box confidence the live detector reports. |

Monitor progress: `GET /api/training/runs` and `GET /api/training/runs/<id>`
(MCP: `list_training_runs`, `get_training_run`) — status moves through `queued`,
`preparing`, `training`, `complete` (or `error` / `stopped`), with per-epoch
progress, metrics, and a log. Stop a run with `POST /api/training/runs/<id>/stop`
(MCP: `stop_training_run`). If TailCam restarts mid-run, the run is marked
`error` so it never appears stuck.

## Models

The **Models** screen lists base, trained, and bring-your-own models:

- **Register BYO** — `POST /api/models` with a `name`, `path` to a `.pt` file, and
  `task` (MCP: `register_model`).
- **Activate** — `POST /api/models/<id>/activate` (MCP: `activate_model`). An
  active model takes over motion analysis from Ollama.
- **Deactivate** — `POST /api/models/deactivate` (MCP: `deactivate_model`) returns
  to the default Ollama analyzer.
- **Delete** — `DELETE /api/models/<id>` (MCP: `delete_model`).

## Detection vs classification

- **Classification** answers "what is in this frame?" — cheaper, smaller, good
  for event labeling.
- **Detection** answers "what and where?" — draws boxes, needs annotated samples
  and larger input sizes. Run the active detector on a live frame with
  `POST /api/cameras/<id>/detect`.

## Agent-driven training

Everything above is exposed over [MCP](mcp-tools), so an agent can create a
dataset, import events, kick off a run, poll it, and activate the model — entirely
from a chat. See [Connecting agents](mcp-connect) for a worked example.
