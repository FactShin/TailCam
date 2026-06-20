import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  useAi,
  useLoadModel,
  useOllamaModels,
  usePullModel,
  usePullProgress,
  useUpdateAi,
} from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, Toggle } from "../components/ui";
import { IconBrain, IconCheck, IconChip, IconCopy, IconDownload, IconMotion, IconSparkle } from "../icons";

// Curated local vision models (all available from the Ollama registry).
const CATALOG: {
  name: string;
  label: string;
  size: string;
  speed: string;
  quality: string;
  recommended?: boolean;
  blurb: string;
}[] = [
  { name: "moondream", label: "Moondream", size: "~1.7 GB", speed: "Fast", quality: "Good", recommended: true,
    blurb: "Tiny and quick — the best default. Runs on almost any machine and labels events in a flash." },
  { name: "llava", label: "LLaVA", size: "~4.7 GB", speed: "Medium", quality: "Better",
    blurb: "Richer, more descriptive labels. A solid step up if you have the RAM to spare." },
  { name: "minicpm-v", label: "MiniCPM-V", size: "~5.5 GB", speed: "Medium", quality: "High",
    blurb: "Excellent detail for its size — sharp labels without a giant download." },
  { name: "llama3.2-vision", label: "Llama 3.2 Vision", size: "~7.9 GB", speed: "Slower", quality: "Best",
    blurb: "Meta's vision model — the most accurate option, happiest on a capable GPU." },
];

const LABELS = ["person", "animal", "vehicle", "package", "plant", "nothing"];

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <button className="cmdbox" onClick={copy} title="Copy command">
      <span className="cmdbox-pfx">$</span>
      <code>{text}</code>
      <span className="cmdbox-copy">{done ? <IconCheck size={14} /> : <IconCopy size={14} />}{label ?? (done ? "Copied" : "Copy")}</span>
    </button>
  );
}

export function AiStudio() {
  const navigate = useNavigate();
  const toast = useToast();
  const ai = useAi().data;
  const models = useOllamaModels().data;
  const pull = usePullProgress().data;
  const update = useUpdateAi();
  const pullModel = usePullModel();
  const loadModel = useLoadModel();

  const reachable = !!(ai?.reachable || models?.reachable);
  const installed = models?.installed ?? [];
  const hasModel = installed.length > 0;
  const enabled = !!ai?.enabled;
  const working = enabled && reachable && !!ai?.model_present;
  const suggested = ai?.model || "moondream";

  const isInstalled = (name: string) =>
    installed.some((m) => m === name || m.startsWith(name + ":"));
  const isActive = (name: string) => ai?.model === name || ai?.model?.startsWith(name + ":");

  const setEnabled = async (on: boolean) => {
    try {
      await update.mutateAsync({ enabled: on });
      toast.ok(on ? "AI analysis on" : "AI analysis off");
    } catch {
      toast.err("Could not update AI");
    }
  };

  const useModel = async (name: string) => {
    try {
      await update.mutateAsync({ model: name, enabled: true });
      toast.ok(`Using ${name}`);
    } catch {
      toast.err("Could not set model");
    }
  };

  const download = async (name: string) => {
    try {
      await pullModel.mutateAsync(name);
      toast.ok(`Downloading ${name}…`);
    } catch {
      toast.err("Ollama isn't reachable — start it first");
    }
  };

  const steps = [
    {
      done: reachable,
      title: "Run Ollama",
      body: reachable
        ? `Connected at ${ai?.base_url}.`
        : "TailCam talks to a local Ollama server. Install it, then start it:",
      commands: reachable ? [] : ["curl -fsSL https://ollama.com/install.sh | sh", "ollama serve"],
    },
    {
      done: hasModel,
      title: "Download a model",
      body: hasModel
        ? `${installed.length} model(s) installed.`
        : "Grab a vision model — download it right here, or run:",
      commands: hasModel ? [] : [`ollama pull ${suggested}`],
    },
    {
      done: enabled,
      title: "Turn on AI analysis",
      body: enabled ? "Motion events are being labeled." : "Flip the switch up top to start labeling motion events.",
      commands: [],
    },
    {
      done: working,
      title: "You're set",
      body: working
        ? "Everything's connected — your cameras now describe what they see."
        : "Once the steps above are green, motion events will be labeled automatically.",
      commands: [],
    },
  ];
  const doneCount = steps.filter((s) => s.done).length;

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Local AI</span></div>
          <h1 className="screen-title">AI</h1>
          <p className="screen-sub">Your cameras describe what they see — entirely on your own hardware.</p>
        </div>
        <div className="head-actions">
          <span className={`ais-status ${working ? "on" : enabled ? "warn" : ""}`}>
            <span className="dot" /> {working ? "Active" : enabled ? "Needs setup" : "Off"}
          </span>
          <Toggle checked={enabled} label="Enable AI analysis" onChange={setEnabled} />
        </div>
      </div>

      {/* Setup checklist */}
      <div className="panel ais-setup">
        <div className="panel-title">
          <IconSparkle size={16} /> Setup
          <span style={{ flex: 1 }} />
          <span className="ais-progress-label">{doneCount}/4</span>
        </div>
        <div className="ais-steps">
          {steps.map((s, i) => (
            <div key={i} className={`ais-step ${s.done ? "done" : ""}`}>
              <div className="ais-step-mark">{s.done ? <IconCheck size={16} /> : i + 1}</div>
              <div className="ais-step-body">
                <div className="ais-step-title">{s.title}</div>
                <p className="ais-step-text">{s.body}</p>
                {s.commands.map((c) => <CopyButton key={c} text={c} />)}
                {i === 1 && !hasModel && reachable && (
                  <Button variant="primary" size="sm" icon={<IconDownload size={14} />}
                    disabled={pull?.active} onClick={() => download(suggested)}>
                    Download {suggested} here
                  </Button>
                )}
                {i === 0 && !reachable && (
                  <a className="ais-link" href="https://ollama.com/download" target="_blank" rel="noreferrer">
                    Get Ollama for your OS ↗
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* How it works */}
      <div className="panel">
        <div className="panel-title"><IconMotion size={16} /> How it works</div>
        <div className="ais-how">
          <div className="ais-how-step"><div className="ais-how-ic"><IconMotion size={20} /></div><div><b>1. Motion fires</b><span>A camera with motion detection sees something move.</span></div></div>
          <div className="ais-how-arrow">→</div>
          <div className="ais-how-step"><div className="ais-how-ic"><IconChip size={20} /></div><div><b>2. Local model looks</b><span>One frame is sent to your Ollama model — never the cloud.</span></div></div>
          <div className="ais-how-arrow">→</div>
          <div className="ais-how-step"><div className="ais-how-ic"><IconSparkle size={20} /></div><div><b>3. You get a label</b><span>The event is tagged with what it saw.</span></div></div>
        </div>
        <div className="ais-labels">
          {LABELS.map((l) => <span key={l} className="ais-label-chip">{l}</span>)}
        </div>
      </div>

      {/* Model catalog */}
      <div className="panel">
        <div className="panel-title"><IconChip size={16} /> Choose a model
          <span style={{ flex: 1 }} />
          <span className={`ais-conn ${reachable ? "on" : "off"}`}>{reachable ? "Ollama connected" : "Ollama offline"}</span>
        </div>
        <p className="ais-intro">Download a model with one click, or copy the command for your terminal. Bigger models are smarter but slower and larger.</p>
        <div className="ais-cards">
          {CATALOG.map((m) => {
            const inst = isInstalled(m.name);
            const active = inst && isActive(m.name);  // "active" only makes sense once installed
            const pulling = pull?.active && pull.model === m.name;
            return (
              <div key={m.name} className={`ais-card ${active ? "active" : ""}`}>
                <div className="ais-card-head">
                  <span className="ais-card-name">{m.label}</span>
                  {m.recommended && <span className="ais-badge rec">Recommended</span>}
                  {active ? <span className="ais-badge active">Active</span>
                    : inst ? <span className="ais-badge ok">Installed</span> : null}
                </div>
                <p className="ais-card-blurb">{m.blurb}</p>
                <div className="ais-chips">
                  <span className="ais-chip">{m.size}</span>
                  <span className="ais-chip">{m.speed}</span>
                  <span className="ais-chip">{m.quality} quality</span>
                </div>

                {pulling ? (
                  <div className="ais-pull">
                    <div className="ais-pull-bar"><span style={{ width: `${pull?.percent ?? 0}%` }} /></div>
                    <span className="ais-pull-text">{pull?.detail || "downloading"} · {pull?.percent ?? 0}%</span>
                  </div>
                ) : active ? (
                  <Button variant="ghost" size="sm" onClick={() => loadModel.mutate(m.name)}>Warm up</Button>
                ) : inst ? (
                  <Button variant="primary" size="sm" onClick={() => useModel(m.name)}>Use this model</Button>
                ) : (
                  <div className="ais-card-actions">
                    <Button variant="primary" size="sm" icon={<IconDownload size={14} />}
                      disabled={!reachable || pull?.active} onClick={() => download(m.name)}>
                      Download
                    </Button>
                    <CopyButton text={`ollama pull ${m.name}`} label="Copy" />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <ConnectionPanel />

      {/* Train your own */}
      <div className="panel ais-train">
        <div className="ais-train-ic"><IconBrain size={20} /></div>
        <div className="ais-train-text">
          <b>Want more than labels?</b>
          <span>Train a private model on your own camera footage — draw boxes, fine-tune, and run it live.</span>
        </div>
        <Button variant="ghost" onClick={() => navigate("/training")}>Open Training →</Button>
      </div>

      <p className="ais-foot">Everything here runs on hardware you control. No cloud, no accounts, nothing leaves your tailnet.</p>
    </div>
  );
}

function ConnectionPanel() {
  const ai = useAi().data;
  const update = useUpdateAi();
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (ai && !dirty) setUrl(ai.base_url);
  }, [ai, dirty]);

  const save = async () => {
    try {
      await update.mutateAsync({ base_url: url.trim() || undefined });
      setDirty(false);
      toast.ok("Saved — re-checking Ollama");
    } catch {
      toast.err("Could not save");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title"><IconChip size={16} /> Where is Ollama running?</div>
      <p className="ais-intro">
        On this machine? Leave it as <span className="mono lit">localhost</span>. Running Ollama on a beefier
        box (e.g. a Mac mini)? Point this at its tailnet address so one machine can analyze the whole fleet.
      </p>
      <div className="ais-conn-form">
        <input className="tl-input" value={url} placeholder="http://localhost:11434"
          onChange={(e) => { setUrl(e.target.value); setDirty(true); }} />
        <Button variant="primary" disabled={!dirty || update.isPending} onClick={save}>Save &amp; test</Button>
      </div>
    </div>
  );
}
