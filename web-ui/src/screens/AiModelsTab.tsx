import { useEffect, useState } from "react";

import {
  useActivateModel,
  useAi,
  useDeactivateModel,
  useDeleteModel,
  useLoadModel,
  useModels,
  useOllamaModels,
  usePostprocess,
  usePullModel,
  usePullProgress,
  useRegisterModel,
  useSetPostprocess,
  useUpdateAi,
} from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog } from "../components/ui";
import { IconCheck, IconChip, IconCopy, IconDownload, IconSparkle, IconTrash } from "../icons";
import { copyToClipboard } from "../lib/clipboard";
import { fmtAgo } from "../lib/format";
import type { DatasetTask, EngineInfo, ModelInfo } from "../types";

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

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    // copyToClipboard falls back to execCommand over http:// (no secure ctx).
    if (await copyToClipboard(text)) {
      setDone(true);
      setTimeout(() => setDone(false), 1400);
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

/** Everything model-shaped in one tab: Ollama vision models (catalog + anything
 * else installed), the local trained/BYO registry, the Ollama connection, and
 * the timelapse interpolation engines. */
export function AiModelsTab() {
  return (
    <>
      <OllamaCatalog />
      <LocalRegistry />
      <ConnectionPanel />
      <EnginesPanel />
    </>
  );
}

// ------------------------------------------------------------- Ollama models
function OllamaCatalog() {
  const toast = useToast();
  const ai = useAi().data;
  const models = useOllamaModels().data;
  const pull = usePullProgress().data;
  const update = useUpdateAi();
  const pullModel = usePullModel();
  const loadModel = useLoadModel();

  const reachable = !!(ai?.reachable || models?.reachable);
  const installed = models?.installed ?? [];
  const usingOllama = ai?.pipeline?.mode !== "local";

  const isInstalled = (name: string) =>
    installed.some((m) => m === name || m.startsWith(name + ":"));
  const isActive = (name: string) => ai?.model === name || ai?.model?.startsWith(name + ":");
  const catalogNames = CATALOG.map((c) => c.name);
  // Models the user pulled themselves — make them selectable, not invisible.
  const others = installed.filter(
    (m) => !catalogNames.some((c) => m === c || m.startsWith(c + ":")),
  );

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

  return (
    <div className="panel">
      <div className="panel-title"><IconChip size={16} /> Ollama vision models
        <span style={{ flex: 1 }} />
        <span className={`ais-conn ${reachable ? "on" : "off"}`}>{reachable ? "Ollama connected" : "Ollama offline"}</span>
      </div>
      <p className="ais-intro">
        These label motion events{usingOllama ? "" : " when no trained model is active"}. Download with
        one click, or copy the command. Bigger models are smarter but slower and larger.
      </p>
      {!reachable && (
        <div className="ais-offline-help">
          <CopyButton text="curl -fsSL https://ollama.com/install.sh | sh" />
          <CopyButton text="ollama serve" />
          <a className="ais-link" href="https://ollama.com/download" target="_blank" rel="noreferrer">
            Get Ollama for your OS ↗
          </a>
        </div>
      )}
      <div className="ais-cards">
        {CATALOG.map((m) => {
          const inst = isInstalled(m.name);
          const active = inst && isActive(m.name);
          const pulling = pull?.active && pull.model === m.name;
          return (
            <div key={m.name} className={`ais-card ${active ? "active" : ""}`}>
              <div className="ais-card-head">
                <span className="ais-card-name">{m.label}</span>
                {m.recommended && <span className="ais-badge rec">Recommended</span>}
                {active ? <span className="ais-badge active">Selected</span>
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

      {others.length > 0 && (
        <div className="ais-others">
          <span className="microlabel">Also installed on Ollama</span>
          <div className="ais-other-list">
            {others.map((m) => (
              <div key={m} className="ais-other-row">
                <span className="mono">{m}</span>
                {isActive(m.split(":")[0]) || ai?.model === m ? (
                  <span className="ais-badge active">Selected</span>
                ) : (
                  <Button variant="outline" size="sm" onClick={() => useModel(m)}>Use</Button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------- Local registry
function LocalRegistry() {
  const ai = useAi().data;
  const models = useModels().data ?? [];
  const activate = useActivateModel();
  const deactivate = useDeactivateModel();
  const register = useRegisterModel();
  const del = useDeleteModel();
  const toast = useToast();
  const [byoName, setByoName] = useState("");
  const [byoPath, setByoPath] = useState("");
  const [byoTask, setByoTask] = useState<DatasetTask>("classification");
  const [confirm, setConfirm] = useState<ModelInfo | null>(null);

  const localActive = ai?.pipeline?.mode === "local";
  const activeModelId = models.find((m) => m.active)?.id ?? 0;

  const onRegister = async () => {
    if (!byoPath.trim()) return;
    try {
      await register.mutateAsync({ name: byoName.trim() || "My model", path: byoPath.trim(), task: byoTask });
      setByoName(""); setByoPath("");
      toast.ok("Model added");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not add model");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title"><IconChip size={16} /> Your models
        <span style={{ flex: 1 }} />
        <span className="kv-v mono" style={{ fontSize: 11, color: "var(--muted)" }}>
          Analyzing with: {localActive ? (ai?.pipeline?.model_name || "your model") : "Ollama"}
        </span>
      </div>
      <p className="ais-intro">
        Models you've <b>trained</b> (in the Training tab) or <b>brought</b> as a <span className="mono">.pt</span> file.
        Activating one takes over analysis from Ollama.
      </p>

      <div className="model-list">
        {models.map((m) => (
          <div key={m.id} className={`model-row ${m.active ? "is-active" : ""}`}>
            <div className="model-main">
              <span className="model-name">{m.name}</span>
              <span className="model-meta mono">
                <span className={`badge ${m.kind === "base" ? "badge-accent" : m.kind === "trained" ? "badge-ok" : "badge-warn"}`}>{m.kind}</span>
                {m.task === "detection" ? <span className="badge badge-accent">detection</span> : null}
                {m.base_model ? ` · ${m.base_model}` : ""} · {fmtAgo(m.created_ts)}
              </span>
            </div>
            <div className="model-actions">
              {m.active ? (
                <span className="badge badge-ok">Active</span>
              ) : (
                <Button variant="outline" size="sm" disabled={activate.isPending} onClick={() => activate.mutate(m.id)}>Use</Button>
              )}
              {m.kind !== "base" && (
                <Button variant="ghost" size="sm" icon={<IconTrash size={14} />} onClick={() => setConfirm(m)} aria-label="Delete model">{""}</Button>
              )}
            </div>
          </div>
        ))}
      </div>
      <button className="model-default-btn" onClick={() => deactivate.mutate()} disabled={!activeModelId}>
        Use default analyzer (Ollama)
      </button>

      <div className="ai-form" style={{ marginTop: 16 }}>
        <label className="tl-field">
          <span className="microlabel">Add your model — name</span>
          <input className="tl-input" value={byoName} placeholder="My model" onChange={(e) => setByoName(e.target.value)} />
        </label>
        <label className="tl-field">
          <span className="microlabel">Path to .pt file (on this device)</span>
          <input className="tl-input" value={byoPath} placeholder="/path/to/model.pt" onChange={(e) => setByoPath(e.target.value)} />
        </label>
        <label className="tl-field">
          <span className="microlabel">Task</span>
          <select className="tl-select" value={byoTask} onChange={(e) => setByoTask(e.target.value as DatasetTask)}>
            <option value="classification">Classification</option>
            <option value="detection">Detection</option>
          </select>
        </label>
        <div className="tl-field tl-field-action">
          <Button variant="primary" disabled={!byoPath.trim() || register.isPending} onClick={onRegister}>Add</Button>
        </div>
      </div>

      <ConfirmDialog
        open={!!confirm}
        title="Delete model?"
        danger
        confirmLabel="Delete"
        body={confirm ? `"${confirm.name}" will be removed from the registry.` : ""}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          if (!confirm) return;
          const id = confirm.id; setConfirm(null);
          try { await del.mutateAsync(id); toast.ok("Deleted"); } catch { toast.err("Delete failed"); }
        }}
      />
    </div>
  );
}

// -------------------------------------------------------- Ollama connection
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

// ------------------------------------------------------ Timelapse engines
function EngineCard({
  engine, isDefault, onMakeDefault,
}: { engine: EngineInfo; isDefault: boolean; onMakeDefault: () => void }) {
  return (
    <div className={`engine-card ${isDefault ? "is-default" : ""}`}>
      <div className="engine-head">
        <span className="engine-name">{engine.label}</span>
        {engine.available ? (
          <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> Ready</span>
        ) : (
          <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> Not installed</span>
        )}
      </div>
      <div className="engine-meta mono">
        {engine.id === "ffmpeg"
          ? `Motion interpolation · ${engine.source}${engine.version ? ` · ${engine.version}` : ""}`
          : engine.available
            ? "GPU frame interpolation · higher quality"
            : "Optional GPU engine — install to enable"}
      </div>
      {engine.id === "rife" && !engine.available && (
        <div className="engine-hint mono">
          Download <span className="lit">rife-ncnn-vulkan</span> for your OS, then add it to your PATH
          (or set <span className="lit">[timelapse] rife_path</span> in config.toml).
        </div>
      )}
      <div className="engine-foot">
        {isDefault ? (
          <span className="badge badge-accent">Default engine</span>
        ) : (
          <button className="btn btn-outline btn-sm" disabled={!engine.available} onClick={onMakeDefault}>
            Make default
          </button>
        )}
      </div>
    </div>
  );
}

function EnginesPanel() {
  const toast = useToast();
  const pp = usePostprocess().data;
  const setPp = useSetPostprocess();

  const makeDefault = async (id: string) => {
    try {
      await setPp.mutateAsync({ default_engine: id });
      toast.ok(`Default engine: ${id}`);
    } catch {
      toast.err("Could not change engine");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title"><IconSparkle size={16} /> Timelapse interpolation</div>
      <p className="engine-intro">
        The engine used by <b>Smooth</b> on the Timelapse page. ffmpeg works everywhere; RIFE is an
        optional GPU model with higher quality. A failed RIFE run falls back to ffmpeg automatically.
      </p>
      <div className="engine-grid">
        {(pp?.engines ?? []).map((e) => (
          <EngineCard key={e.id} engine={e} isDefault={pp?.default_engine === e.id} onMakeDefault={() => makeDefault(e.id)} />
        ))}
      </div>
    </div>
  );
}
