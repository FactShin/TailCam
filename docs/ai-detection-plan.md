# AnyCam — AI Detection Roadmap (Phase: "Smart Motion")

> Status: PLANNED. Prereq fix shipped in v0.2.7 (motion events no longer leak
> "ongoing"). This phase turns AnyCam's pixel-diff motion into a Blink/Ring-style
> smart detection system, with a path to custom detectors (3D-printer failure).

## Vision (user's words)
1. **Now-ish:** security-camera-grade motion detection (like Blink/Ring) — fewer
   false alarms, "a person walked by" vs "the sun moved", event clips you can trust.
2. **Next:** AI understanding of events via a local **Ollama** vision model — no
   cloud, runs on the user's own hardware on the tailnet.
3. **Future:** special-purpose detectors, starting with **3D-print failure
   detection** (spaghetti detection) — the original reason this app exists.

## Architecture: a pluggable analyzer pipeline

Today: `MotionWorker` samples frames → `MotionDetector` (absdiff) → event log.
Add a second, *smarter* stage that runs only when cheap motion fires:

```
frames ──> Stage 1: pixel motion (cheap, existing absdiff)   ~every 200ms
              │  motion?
              ▼
           Stage 2: Analyzer (pluggable, slow ok)            ~1-2 frames/event
              ├─ "ollama"  → local vision model (llava / qwen2.5-vl / moondream)
              │              "Is there a person/animal/vehicle? Describe briefly."
              └─ (future) "printguard" → 3D-print failure classifier
              ▼
           Event enrichment: label ("person", "cat", "false alarm"),
           description, confidence → stored on the motion event
```

Key principle: **Stage 1 gates Stage 2.** The vision model is only consulted on
motion (a frame or two per event), so a modest box (or the Mac mini) can serve
the whole tailnet. Analyzer failures degrade gracefully to plain motion events.

## Build plan

### Step 1 — Smarter events (no AI yet)
- Persist `label`, `description`, `confidence` columns on motion_events (schema v2).
- Event clip improvements: pre-roll buffer (keep last N seconds of frames so the
  clip includes the moments *before* motion), min-event-length, per-camera
  motion zones (ignore regions — UI draws rectangles) and sensitivity already exists.
- UI: event cards show thumbnail of the triggering frame (store a snapshot per event).

### Step 2 — Ollama analyzer  ✅ DONE (v0.3.0)
- `config [ai]`: `enabled`, `base_url` (default `http://localhost:11434`),
  `model` (default `qwen2.5-vl` or `moondream` for small machines), `prompt`
  (default person/animal/vehicle classification), `timeout`.
- `analyzer.py`: POST frame JPEG (base64) to Ollama `/api/generate` (or
  `/api/chat` with images), parse a constrained JSON answer
  `{label, confidence, description}`. Strict timeout; on failure → label "motion".
- The Ollama box can be ANY tailnet node (e.g. the Mac mini serves the Pi's
  events): `base_url` may point at a tailnet host. Document GPU-less guidance
  (moondream ~2GB, fast on Apple Silicon).
- `MotionWorker`: on event open, snapshot the trigger frame → queue for analysis
  (thread, non-blocking) → enrich the event row when the answer lands.
- UI: label chips on event rows ("🧍 person", "🐱 animal", "❓ motion"), filter by
  label, description in the event detail. Optional "alert only on person".

### Step 3 — Notifications (the Ring/Blink feel)
- Pluggable notifiers: start with **ntfy.sh self-hosted / webhook / Apple
  Shortcuts URL** (simple POST when an event with label X happens). Config:
  `notify.url`, `notify.min_label` (e.g. person only), quiet hours.

### Step 4 — 3D-printer mode (future)
- Same Analyzer interface, different backend: either an Ollama prompt tuned for
  print failure ("does this print show spaghetti/detachment/stringing?") as v0,
  or a small dedicated ONNX classifier later (e.g. trained on spaghetti datasets).
- Per-camera analyzer selection: `camera.analyzer = "security" | "printer"`.
- Printer-specific events: "print failure suspected" + auto-pause hook (POST to
  OctoPrint/Moonraker API — the user runs OctoPi already).

## Verification strategy
- Analyzer unit tests with a fake Ollama server (httpx MockTransport) returning
  canned JSON; malformed/timeout cases degrade to "motion".
- E2E: synthetic camera (moving square) + fake Ollama → events get labels.
- Real-world: user runs Ollama on the Mac mini, points `[ai] base_url` at it.

## Open questions for the user (ask before building Step 2)
- Which machine will run Ollama (Mac mini M-series = best fit)?
- Notification target preference (ntfy app, iOS Shortcut, plain webhook)?
- Person-only alerts or all labeled events?
