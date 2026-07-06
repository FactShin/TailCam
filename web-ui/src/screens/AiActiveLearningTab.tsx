import { useState } from "react";

import {
  useActiveLearning,
  useCameras,
  useDatasets,
  useFinetuneBackends,
  useLabelingBackends,
  useLabelStudioProjects,
  useRuns,
  useStartActiveLearning,
  useStartActiveLearningTrain,
  useStopActiveLearning,
  useSyncActiveLearning,
  useTestLabelStudio,
  useUpdateActiveLearning,
} from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, ControlSlider, Spinner, Toggle } from "../components/ui";
import { IconBolt, IconBrain, IconCheck, IconChip, IconMotion, IconRefresh, IconSparkle, IconStop } from "../icons";
import type { ActiveLearningInfo, ActiveLearningSettings } from "../types";

/** Human-in-the-loop active learning: a model watches your cameras, keeps the
 * confident detections, and sends only the uncertain frames to Label Studio
 * for you to label. Synced labels fine-tune the model of your choice. */
export function AiActiveLearningTab() {
  const al = useActiveLearning().data;
  const update = useUpdateActiveLearning();
  const toast = useToast();

  const set = async (body: ActiveLearningSettings, msg?: string) => {
    try {
      await update.mutateAsync(body);
      if (msg) toast.ok(msg);
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not update settings");
    }
  };

  if (!al) return <div className="panel"><Spinner /></div>;

  const steps = [
    { n: 1, label: "Configure", done: al.token_set },
    { n: 2, label: "Watch", done: al.frames_processed > 0 || al.running },
    { n: 3, label: "Review", done: al.review_completed > 0 },
    { n: 4, label: "Sync", done: al.annotated_samples > 0 },
    { n: 5, label: "Fine-tune", done: false },
  ];

  return (
    <>
      <div className="panel ais-flow">
        {steps.map((s, i) => (
          <div key={s.n} className="ais-flow-item">
            <span className={`ais-flow-step ${s.done ? "done" : ""}`}>
              <span className="ais-flow-num">{s.done ? <IconCheck size={13} /> : s.n}</span>
              {s.label}
            </span>
            {i < steps.length - 1 && <span className="ais-flow-arrow">→</span>}
          </div>
        ))}
        <span style={{ flex: 1 }} />
        <span className="ais-flow-meta mono">
          dataset v{al.dataset_version} · {al.annotated_samples} annotated
        </span>
      </div>

      <ModelsPanel al={al} set={set} />
      <LabelStudioPanel al={al} set={set} />
      <RunPanel al={al} set={set} />
      <ReviewSyncPanel al={al} />
      <FinetunePanel al={al} set={set} />
    </>
  );
}

type SetFn = (body: ActiveLearningSettings, msg?: string) => Promise<void>;

// ------------------------------------------------------------ 1. Models & source
function ModelsPanel({ al, set }: { al: ActiveLearningInfo; set: SetFn }) {
  const backends = useLabelingBackends().data ?? [];
  const cameras = useCameras().data ?? [];
  const datasets = useDatasets().data ?? [];
  const detectionDatasets = datasets.filter((d) => d.task === "detection");
  const selected = backends.find((b) => b.id === al.labeling_model);

  return (
    <div className="panel">
      <div className="panel-title"><IconChip size={16} /> 1 · Watcher &amp; source</div>
      <p className="engine-intro">
        Pick the model that watches and pre-labels frames, and where the frames come from.
        Confident detections become machine labels automatically; anything uncertain goes to
        Label Studio for you.
      </p>
      <div className="ai-form">
        <label className="tl-field">
          <span className="microlabel">Labeling / monitoring model</span>
          <select
            className="tl-select"
            value={al.labeling_model}
            onChange={(e) => set({ labeling_model: e.target.value })}
          >
            {backends.map((b) => (
              <option key={b.id} value={b.id} disabled={!b.available}>
                {b.name}{b.available ? "" : " — unavailable"}
              </option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span className="microlabel">Frame source</span>
          <select
            className="tl-select"
            value={al.source}
            onChange={(e) => set({ source: e.target.value })}
          >
            <option value="cameras">All online cameras</option>
            {cameras.map((c) => (
              <option key={c.id} value={`camera:${c.id}`}>Camera: {c.name}</option>
            ))}
            {detectionDatasets.map((d) => (
              <option key={d.id} value={`dataset:${d.id}`}>Dataset: {d.name} ({d.sample_count})</option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span className="microlabel">Interval (s)</span>
          <input
            className="tl-input"
            type="number"
            min={1}
            step={1}
            defaultValue={al.interval_seconds}
            onBlur={(e) => set({ interval_seconds: Math.max(1, Number(e.target.value) || 10) })}
          />
        </label>
      </div>
      {selected && !selected.available && (
        <p className="ais-pipe-err">{selected.name}: {selected.detail}</p>
      )}
      {selected?.available && selected.detail && selected.detail !== "ready" && (
        <span className="help-foot mono">{selected.name}: {selected.detail}</span>
      )}
      {selected && !selected.boxes && (
        <span className="help-foot mono">
          This model labels whole frames (no boxes) — reviews start from a full-frame region.
        </span>
      )}
      <div className="ais-det-tune">
        <ControlSlider
          label="Confidence threshold"
          value={Math.round(al.confidence_threshold * 100)}
          min={5}
          max={95}
          step={5}
          unit="%"
          onChange={(v) => set({ confidence_threshold: v / 100 })}
        />
        <label className="ctl-row" style={{ marginTop: 6 }}>
          <span className="ctl-row-label">Also review frames where the model sees nothing</span>
          <Toggle
            checked={al.review_empty_frames}
            label="Review empty frames"
            onChange={(v) => set({ review_empty_frames: v })}
          />
        </label>
      </div>
      <span className="help-foot mono">
        Detections at or above {Math.round(al.confidence_threshold * 100)}% are auto-labeled;
        frames with anything below it go to Label Studio for human review.
      </span>
    </div>
  );
}

// ------------------------------------------------------------ 2. Label Studio
function LabelStudioPanel({ al, set }: { al: ActiveLearningInfo; set: SetFn }) {
  const toast = useToast();
  const test = useTestLabelStudio();
  const [url, setUrl] = useState(al.label_studio_url);
  const [token, setToken] = useState("");
  const status = test.data;
  const projects = useLabelStudioProjects(!!status?.connected).data ?? [];

  const chip = status
    ? status.connected
      ? <span className="badge badge-ok">connected</span>
      : <span className="badge badge-err">not connected</span>
    : al.token_set
      ? <span className="badge">not tested</span>
      : <span className="badge badge-warn">token needed</span>;

  const save = async () => {
    const body: ActiveLearningSettings = { label_studio_url: url.trim() };
    if (token.trim()) body.label_studio_token = token.trim();
    await set(body);
    setToken("");
    test.mutate(undefined, {
      onSuccess: (s) => (s.connected ? toast.ok("Label Studio connected") : toast.err(s.error)),
    });
  };

  return (
    <div className="panel">
      <div className="panel-title">
        <IconSparkle size={16} /> 2 · Label Studio {chip}
      </div>
      <p className="engine-intro">
        Uncertain frames are reviewed in Label Studio — draw as many boxes per image as you
        need. Run it anywhere (<span className="lit">pip install label-studio &amp;&amp; label-studio start</span>),
        then paste the API token from Account &amp; Settings → Access Token.
      </p>
      <div className="ai-form" style={{ gridTemplateColumns: "1.2fr 1.2fr auto" }}>
        <label className="tl-field">
          <span className="microlabel">Label Studio URL</span>
          <input
            className="tl-input"
            value={url}
            placeholder="http://localhost:8080"
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
        <label className="tl-field">
          <span className="microlabel">API token {al.token_set ? "(saved)" : ""}</span>
          <input
            className="tl-input"
            type="password"
            value={token}
            placeholder={al.token_set ? "••••••••  (leave blank to keep)" : "paste token"}
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
        <div className="tl-field tl-field-action">
          <Button variant="primary" disabled={test.isPending} onClick={save}>
            {test.isPending ? <>Testing… <Spinner size={14} /></> : "Save & test"}
          </Button>
        </div>
      </div>
      {status && !status.connected && <p className="ais-pipe-err">{status.error}</p>}
      <div className="ai-form" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <label className="tl-field">
          <span className="microlabel">Project</span>
          <select
            className="tl-select"
            value={al.project_id}
            onChange={(e) => set({ project_id: Number(e.target.value) })}
          >
            <option value={0}>Create "{al.project_name}" automatically</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.title} ({p.task_count} tasks)</option>
            ))}
            {al.project_id !== 0 && !projects.some((p) => p.id === al.project_id) && (
              <option value={al.project_id}>Project #{al.project_id}</option>
            )}
          </select>
        </label>
        <label className="tl-field">
          <span className="microlabel">New project name</span>
          <input
            className="tl-input"
            defaultValue={al.project_name}
            onBlur={(e) => e.target.value.trim() && set({ project_name: e.target.value.trim() })}
          />
        </label>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ 3. Run + status
function RunPanel({ al, set }: { al: ActiveLearningInfo; set: SetFn }) {
  const start = useStartActiveLearning();
  const stop = useStopActiveLearning();
  const toast = useToast();

  const onStart = async () => {
    try {
      await start.mutateAsync();
      toast.ok("Active learning started");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not start");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">
        <IconMotion size={16} /> 3 · Active learning
        <span style={{ flex: 1 }} />
        {al.running ? (
          <Button variant="outline" icon={<IconStop size={14} />} disabled={stop.isPending}
            onClick={() => stop.mutate()}>
            Stop
          </Button>
        ) : (
          <Button variant="primary" icon={<IconBolt size={15} />} disabled={start.isPending}
            onClick={onStart}>
            {start.isPending ? <>Starting… <Spinner size={14} /></> : "Start Active Learning"}
          </Button>
        )}
      </div>
      {al.running ? (
        <p className="engine-intro">
          ● Watching — <b>{al.labeling_model}</b> at ≥{Math.round(al.confidence_threshold * 100)}%
          confidence. Label the uncertain frames in Label Studio whenever you like.
        </p>
      ) : (
        <p className="engine-intro">
          One click starts the loop: capture frames, run the labeling model, keep confident
          detections, send uncertain ones for review.
        </p>
      )}
      <div className="ai-form" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        <Stat label="Frames processed" value={al.frames_processed} />
        <Stat label="Auto-labeled" value={al.auto_labeled} />
        <Stat label="Sent for review" value={al.sent_for_review} />
        <Stat label="Awaiting labels" value={al.review_pending} />
        <Stat label="Human-labeled" value={al.review_completed} />
      </div>
      {al.last_error && <p className="ais-pipe-err">Heads up: {al.last_error}</p>}
      {al.max_review_per_session > 0 && (
        <span className="help-foot mono">
          Review cap: {al.max_review_per_session} frames/session — raise it in config
          ([active_learning] max_review_per_session).
        </span>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="tl-field">
      <span className="microlabel">{label}</span>
      <span className="mono" style={{ fontSize: 20 }}>{value}</span>
    </div>
  );
}

// ------------------------------------------------------------ 4. Sync
function ReviewSyncPanel({ al }: { al: ActiveLearningInfo }) {
  const sync = useSyncActiveLearning();
  const toast = useToast();

  const onSync = async () => {
    try {
      const r = await sync.mutateAsync();
      toast.ok(
        r.completed
          ? `Synced ${r.completed} annotation${r.completed === 1 ? "" : "s"} (dataset v${r.dataset_version})`
          : `Nothing new — ${r.pending} still awaiting labels`,
      );
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Sync failed");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">
        <IconRefresh size={16} /> 4 · Sync annotations
        <span style={{ flex: 1 }} />
        <Button variant="outline" disabled={sync.isPending} onClick={onSync}>
          {sync.isPending ? <>Syncing… <Spinner size={14} /></> : "Sync from Label Studio"}
        </Button>
      </div>
      <p className="engine-intro">
        Pull your completed Label Studio annotations back into the dataset —
        {" "}{al.review_pending} frame{al.review_pending === 1 ? "" : "s"} currently awaiting labels.
        Synced boxes land on the samples automatically; no files to move.
      </p>
    </div>
  );
}

// ------------------------------------------------------------ 5. Fine-tune
function FinetunePanel({ al, set }: { al: ActiveLearningInfo; set: SetFn }) {
  const backends = useFinetuneBackends().data ?? [];
  const train = useStartActiveLearningTrain();
  const runs = useRuns().data ?? [];
  const toast = useToast();
  const [epochs, setEpochs] = useState(0);

  const selected = backends.find((b) => b.id === al.finetune_model);
  const canTrain = !!selected?.available && al.training_ready && !train.isPending;
  const activeRun = runs.find((r) => ["queued", "preparing", "training"].includes(r.status));

  const onTrain = async () => {
    try {
      await train.mutateAsync(epochs > 0 ? { epochs } : {});
      toast.ok("Fine-tuning started — follow it under Training → runs");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not start fine-tuning");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">
        <IconBrain size={16} /> 5 · Fine-tune
        <span style={{ flex: 1 }} />
        {al.training_ready ? (
          <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> {al.annotated_samples} annotated — ready</span>
        ) : (
          <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> label some frames first</span>
        )}
      </div>
      <p className="engine-intro">
        Fine-tune on everything labeled so far (machine + human). The finished model appears
        under <b>Models → Your models</b> and can be the watcher for the next round — that's the
        loop that makes your homemade model better over time.
      </p>
      <div className="ai-form" style={{ gridTemplateColumns: "1.4fr 1fr auto" }}>
        <label className="tl-field">
          <span className="microlabel">Model to fine-tune</span>
          <select
            className="tl-select"
            value={al.finetune_model}
            onChange={(e) => set({ finetune_model: e.target.value })}
          >
            {backends.map((b) => (
              <option key={b.id} value={b.id}>{b.name}{b.available ? "" : " — unavailable"}</option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span className="microlabel">Epochs (0 = default)</span>
          <input className="tl-input" type="number" min={0} max={300} value={epochs}
            onChange={(e) => setEpochs(Math.max(0, Math.min(300, Number(e.target.value) || 0)))} />
        </label>
        <div className="tl-field tl-field-action">
          <Button variant="primary" icon={<IconBrain size={15} />} disabled={!canTrain} onClick={onTrain}>
            Fine-tune
          </Button>
        </div>
      </div>
      {selected && (
        <span className={selected.available ? "help-foot mono" : "ais-pipe-err"}>
          {selected.name}: {selected.detail}
        </span>
      )}
      {activeRun && (
        <span className="help-foot mono">
          A run is in progress ({activeRun.base_model} · {activeRun.status}) — watch it in the
          Training tab.
        </span>
      )}
    </div>
  );
}
