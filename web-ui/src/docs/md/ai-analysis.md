# AI analysis

TailCam can label motion events with a **local** vision model via
[Ollama](https://ollama.com). Nothing is sent to a cloud service — the model runs
on a machine you control, on your tailnet. Cheap pixel [motion](motion-detection)
gates the model, so it's only consulted a frame or two per event.

> **The easiest way to set this up is AI Studio.** Open **AI Studio** in the
> nav: it shows a live setup checklist (with the exact commands), lets you
> **download a model with one click**, pick which model to use, and enable
> analysis — without leaving the UI. This page is the deeper reference.

The **Overview** tab always shows what is analyzing frames *right now* — your trained model or Ollama — and calls out problems (model missing, Ollama down, a selected model that failed to load). Use **Try it now** to analyze a single live frame through the real pipeline without waiting for motion.

## How it works

1. Motion produces an event with a representative frame.
2. If AI is enabled, TailCam sends that single frame to Ollama's `/api/generate`
   with a constrained JSON prompt.
3. The model returns a `label` (one of `person`, `animal`, `vehicle`, `package`,
   `plant`, `nothing`), a `confidence` (0–1), and a short `description`.
4. The event is annotated with these and shown on the **Events** screen.

## Setting up Ollama

1. Install Ollama on a machine (the same box as TailCam, or any tailnet host with
   a GPU that should analyze the whole fleet's events).
2. Pull a vision model:

   ```bash
   ollama pull moondream
   ```

   `moondream` is small and fast. `qwen2.5vl` or `llava` give better labels at
   higher cost.
3. Point TailCam at it (AI Studio → Models → "Where is Ollama running?", or `[ai]` in [config](configuration)):

   - `base_url` — e.g. `http://localhost:11434`, or
     `http://mac-mini.your-tailnet.ts.net:11434` to use one machine for the fleet.
   - `model` — e.g. `moondream`.
   - `enabled` — turn analysis on.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | false | Master switch for AI analysis. |
| `base_url` | http://localhost:11434 | Ollama endpoint. |
| `model` | moondream | Vision model name. |
| `timeout` | 20.0 | Per-request timeout (seconds). |
| `prompt` | (built-in) | The constrained JSON instruction sent with each frame. |

## Checking status

The **Settings** screen shows whether Ollama is reachable and whether the model
is present. Via API: `GET /api/ai` returns `enabled`, `reachable`, `model`,
`model_present`, and `base_url`.

## Driving it from an agent

The [MCP](mcp-overview) server exposes granular AI control so an agent can stand
up local AI end to end:

- `list_ollama_models` — what's installed and whether Ollama is reachable.
- `pull_ollama_model` — download a model (confirm required; can take minutes).
- `load_ollama_model` — warm/"start" a model into memory for fast first inference.
- `set_ai_config` — enable analysis and choose the model/base URL.
- `test_ai_connection` — verify reachability and model presence.

See [MCP tools](mcp-tools).

## Fleet analyzer pattern

Because `base_url` can point at any tailnet host, a single machine with a capable
GPU can analyze events for your whole [fleet](fleet). Point each node's `[ai]`
`base_url` at that host's Ollama, and every node's motion events get labeled by
the same model.

## From labels to your own model

Ollama labeling is a great default. When you want faster, cheaper, or more
specific detection, collect your labeled events into a dataset and train a local
model — see [Training](training). An active trained model takes over motion
analysis from Ollama automatically.
