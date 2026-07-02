import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAi, useAiTest, useCameras, useDetectionInfo, useOllamaModels, useUpdateAi, useUpdateDetection } from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, ControlSlider, Segmented, Spinner, Toggle } from "../components/ui";
import { IconBrain, IconCheck, IconChip, IconMotion, IconSparkle } from "../icons";
import { AiModelsTab } from "./AiModelsTab";
import { AiTrainingTab } from "./AiTrainingTab";

export const AI_LABELS = ["person", "animal", "vehicle", "package", "plant", "nothing"];

type Tab = "overview" | "models" | "training";

/** One place for everything AI: status + testing (Overview), Ollama & local
 * model management (Models), and dataset/training workflow (Training). */
export function AiStudio() {
  const [params, setParams] = useSearchParams();
  const tab = (["overview", "models", "training"].includes(params.get("tab") ?? "")
    ? params.get("tab")
    : "overview") as Tab;
  const setTab = (t: Tab) =>
    setParams(t === "overview" ? {} : { tab: t }, { replace: true });

  const toast = useToast();
  const ai = useAi().data;
  const update = useUpdateAi();
  const enabled = !!ai?.enabled;

  const setEnabled = async (on: boolean) => {
    try {
      await update.mutateAsync({ enabled: on });
      toast.ok(on ? "AI analysis on" : "AI analysis off");
    } catch {
      toast.err("Could not update AI");
    }
  };

  const pipe = ai?.pipeline;
  const running = pipe?.mode === "local" || pipe?.mode === "builtin" || (pipe?.mode === "ollama" && !!ai?.model_present && !!ai?.reachable);

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Local AI</span></div>
          <h1 className="screen-title">AI Studio</h1>
          <p className="screen-sub">Understand, run, and train camera AI — entirely on your own hardware.</p>
        </div>
        <div className="head-actions">
          <span className={`ais-status ${running ? "on" : enabled || pipe?.mode === "local" ? "warn" : ""}`}>
            <span className="dot" /> {running ? "Active" : enabled || pipe?.mode === "local" ? "Needs attention" : "Off"}
          </span>
          <Toggle checked={enabled} label="Enable AI analysis" onChange={setEnabled} />
        </div>
      </div>

      <div className="ais-tabs">
        <Segmented
          ariaLabel="AI Studio section"
          value={tab}
          onChange={(t) => setTab(t as Tab)}
          options={[
            { value: "overview", label: "Overview" },
            { value: "models", label: "Models" },
            { value: "training", label: "Training" },
          ]}
        />
      </div>

      {tab === "overview" && <OverviewTab onGoto={setTab} />}
      {tab === "models" && <AiModelsTab />}
      {tab === "training" && <AiTrainingTab />}
    </div>
  );
}

// ---------------------------------------------------------------- Overview
function OverviewTab({ onGoto }: { onGoto: (t: Tab) => void }) {
  const ai = useAi().data;
  const models = useOllamaModels().data;

  const pipe = ai?.pipeline;
  const reachable = !!(ai?.reachable || models?.reachable);
  const installed = models?.installed ?? [];
  const enabled = !!ai?.enabled;

  return (
    <>
      <PipelineCard onGoto={onGoto} />
      <ObjectDetectionCard />
      <TestPanel />

      {/* Setup checklist — only while something is missing */}
      {pipe?.mode !== "local" && pipe?.mode !== "builtin" && !(enabled && reachable && ai?.model_present) && (
        <SetupPanel reachable={reachable} hasModel={installed.length > 0} enabled={enabled} suggested={ai?.model || "moondream"} />
      )}

      {/* How it works */}
      <div className="panel">
        <div className="panel-title"><IconMotion size={16} /> How it works</div>
        <div className="ais-how">
          <div className="ais-how-step"><div className="ais-how-ic"><IconMotion size={20} /></div><div><b>1. Motion fires</b><span>A camera with motion detection sees something move.</span></div></div>
          <div className="ais-how-arrow">→</div>
          <div className="ais-how-step"><div className="ais-how-ic"><IconChip size={20} /></div><div><b>2. A local model looks</b><span>One frame goes to your model — Ollama or one you trained. Never the cloud.</span></div></div>
          <div className="ais-how-arrow">→</div>
          <div className="ais-how-step"><div className="ais-how-ic"><IconSparkle size={20} /></div><div><b>3. You get a label</b><span>The event is tagged with what it saw.</span></div></div>
        </div>
        <div className="ais-labels">
          {AI_LABELS.map((l) => <span key={l} className="ais-label-chip">{l}</span>)}
        </div>
      </div>

      {/* Train teaser */}
      <div className="panel ais-train">
        <div className="ais-train-ic"><IconBrain size={20} /></div>
        <div className="ais-train-text">
          <b>Want more than labels?</b>
          <span>Train a private model on your own footage — collect, label, fine-tune, and run it live.</span>
        </div>
        <Button variant="ghost" onClick={() => onGoto("training")}>Open Training →</Button>
      </div>

      <p className="ais-foot">Everything here runs on hardware you control. No cloud, no accounts, nothing leaves your tailnet.</p>
    </>
  );
}

/** The single source of truth: what is analyzing frames right now. */
function PipelineCard({ onGoto }: { onGoto: (t: Tab) => void }) {
  const ai = useAi().data;
  const pipe = ai?.pipeline;
  if (!pipe) return null;

  const mode = pipe.mode;
  const healthy =
    mode === "local" || mode === "builtin" ||
    (mode === "ollama" && !!ai?.reachable && !!ai?.model_present);

  let title: string;
  let detail: string;
  if (mode === "local") {
    title = `Your model: ${pipe.model_name}`;
    detail =
      pipe.task === "detection"
        ? "A model you trained (or brought) is drawing boxes and labeling events."
        : "A model you trained (or brought) is labeling motion events.";
  } else if (mode === "ollama") {
    title = `Ollama: ${pipe.model_name}`;
    detail = !ai?.reachable
      ? "Ollama isn't reachable — events won't be labeled until it's back."
      : !ai?.model_present
        ? `The model "${pipe.model_name}" isn't downloaded yet — grab it in Models.`
        : "Motion events are labeled by your local Ollama model.";
  } else if (mode === "builtin") {
    title = `Built-in detection: ${pipe.model_name || "YOLO"}`;
    detail = "The built-in object detector is drawing boxes and labeling motion events — no setup needed. Enable Ollama or train a model for richer descriptions.";
  } else {
    title = "Analysis is off";
    detail = "Turn on AI analysis (top right) or activate a trained model to start labeling events.";
  }

  return (
    <div className={`panel ais-pipeline ${healthy ? "ok" : "warn"}`}>
      <div className="ais-pipe-main">
        <span className={`ais-pipe-dot ${healthy ? "ok" : mode === "off" ? "" : "warn"}`} />
        <div className="ais-pipe-text">
          <b>{title}</b>
          <span>{detail}</span>
          {pipe.error && (
            <span className="ais-pipe-err">
              Heads up: {pipe.error}{mode === "ollama" ? " — falling back to Ollama." : ""}
            </span>
          )}
        </div>
      </div>
      <div className="ais-pipe-actions">
        <Button variant="outline" size="sm" onClick={() => onGoto("models")}>Manage models</Button>
      </div>
    </div>
  );
}

/** Built-in plug-and-play object detection: one switch, boxes + labels on the
 * live view. The model downloads itself the first time it's needed. */
function ObjectDetectionCard() {
  const toast = useToast();
  const det = useDetectionInfo().data;
  const update = useUpdateDetection();
  if (!det) return null;

  const set = async (body: Parameters<typeof update.mutateAsync>[0], msg?: string) => {
    try {
      await update.mutateAsync(body);
      if (msg) toast.ok(msg);
    } catch {
      toast.err("Could not update detection");
    }
  };

  const chip =
    det.status === "ready" ? <span className="badge badge-ok">ready</span>
    : det.status === "downloading" ? <span className="badge badge-accent">downloading {det.percent ? `${Math.round(det.percent)}%` : "…"}</span>
    : det.status === "error" ? <span className="badge badge-err">error</span>
    : det.enabled ? <span className="badge">starting…</span>
    : <span className="badge">off</span>;

  return (
    <div className="panel">
      <div className="panel-title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <IconChip size={16} /> Object detection
        {chip}
        <span style={{ flex: 1 }} />
        <Toggle
          checked={det.enabled}
          label="Object detection"
          onChange={(v) => set({ enabled: v }, v ? "Object detection on — boxes appear on camera views" : "Object detection off")}
        />
      </div>
      <p className="ais-intro">
        Live bounding boxes with labels — person, cup, bottle, cat, dog and 75 more —
        drawn right on the camera view, and motion events get labeled automatically.
        Nothing to install: the model{det.model ? ` (${det.model})` : ""} downloads itself
        the first time and runs fully on this device.
      </p>
      {det.status === "downloading" && (
        <div className="ais-pull-bar" aria-label="Model download progress">
          <span style={{ width: `${Math.max(3, det.percent)}%` }} />
        </div>
      )}
      {det.status === "error" && (
        <p className="ais-pipe-err">Model setup failed: {det.error} — it retries the next time a camera view asks for boxes.</p>
      )}
      {det.enabled && (
        <div className="ais-det-tune">
          <ControlSlider
            label="Confidence"
            value={Math.round(det.confidence * 100)}
            min={5}
            max={95}
            step={5}
            unit="%"
            onChange={(v) => set({ confidence: v / 100 })}
          />
          <label className="ctl-row" style={{ marginTop: 6 }}>
            <span className="ctl-row-label">Show boxes by default on camera pages</span>
            <Toggle
              checked={det.overlay_default}
              label="Show boxes by default"
              onChange={(v) => set({ overlay_default: v })}
            />
          </label>
        </div>
      )}
    </div>
  );
}

/** Analyze one live frame through the real pipeline — instant validation. */
function TestPanel() {
  const cameras = (useCameras().data ?? []).filter((c) => c.status !== "offline");
  const test = useAiTest();
  const [cam, setCam] = useState("");
  const target = cam || cameras[0]?.id || "";
  const result = test.data;

  return (
    <div className="panel">
      <div className="panel-title"><IconSparkle size={16} /> Try it now</div>
      <p className="ais-intro">
        Analyze a single live frame through the exact pipeline motion events use — no waiting for
        something to move.
      </p>
      <div className="ais-test-row">
        <select className="tl-select" value={target} onChange={(e) => setCam(e.target.value)}>
          {cameras.length === 0 && <option value="">— no cameras online —</option>}
          {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <Button
          variant="primary"
          disabled={!target || test.isPending}
          onClick={() => test.mutate(target)}
        >
          {test.isPending ? <>Analyzing… <Spinner size={14} /></> : "Analyze a frame"}
        </Button>
      </div>
      {result && !test.isPending && (
        result.ok ? (
          <div className="ais-test-result ok">
            <IconCheck size={16} />
            <span className="ais-test-label">{result.label}</span>
            {typeof result.confidence === "number" && (
              <span className="ais-test-conf mono">{Math.round(result.confidence * 100)}%</span>
            )}
            {result.description && <span className="ais-test-desc">{result.description}</span>}
            <span className="ais-test-engine mono">via {result.engine === "local" ? "your model" : "ollama"}</span>
          </div>
        ) : (
          <div className="ais-test-result err">{result.error}</div>
        )
      )}
    </div>
  );
}

function SetupPanel({
  reachable, hasModel, enabled, suggested,
}: { reachable: boolean; hasModel: boolean; enabled: boolean; suggested: string }) {
  const steps = [
    { done: reachable, title: "Run Ollama", body: reachable ? "Connected." : "Install and start Ollama — see the Models tab for one-click setup." },
    { done: hasModel, title: "Download a model", body: hasModel ? "Model installed." : `Grab ${suggested} from the Models tab (one click).` },
    { done: enabled, title: "Turn on AI analysis", body: enabled ? "Analysis is on." : "Flip the switch at the top right." },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  return (
    <div className="panel ais-setup">
      <div className="panel-title">
        <IconSparkle size={16} /> Setup
        <span style={{ flex: 1 }} />
        <span className="ais-progress-label">{doneCount}/{steps.length}</span>
      </div>
      <div className="ais-steps">
        {steps.map((s, i) => (
          <div key={i} className={`ais-step ${s.done ? "done" : ""}`}>
            <div className="ais-step-mark">{s.done ? <IconCheck size={16} /> : i + 1}</div>
            <div className="ais-step-body">
              <div className="ais-step-title">{s.title}</div>
              <p className="ais-step-text">{s.body}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
