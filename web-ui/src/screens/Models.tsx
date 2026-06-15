import { useEffect, useState } from "react";

import { useAi, usePostprocess, useSetPostprocess, useUpdateAi } from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, Toggle } from "../components/ui";
import { IconChip, IconInfo, IconSparkle } from "../icons";
import type { EngineInfo } from "../types";

function EngineCard({
  engine,
  isDefault,
  onMakeDefault,
}: {
  engine: EngineInfo;
  isDefault: boolean;
  onMakeDefault: () => void;
}) {
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
          (or set <span className="lit">[timelapse] rife_path</span> in config.toml). Runs on Windows/macOS GPUs.
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

export function Models() {
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
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Local Models &amp; Engines</span></div>
          <h1 className="screen-title">Models</h1>
          <p className="screen-sub">On-device AI &amp; video engines — nothing leaves your tailnet</p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title"><IconSparkle size={16} /> Timelapse interpolation</div>
        <p className="engine-intro">
          The engine used by <b>Smooth</b> on the Timelapse page to turn captured frames into flowing
          motion. ffmpeg works everywhere; RIFE is an optional GPU model with higher quality. A failed
          RIFE run falls back to ffmpeg automatically.
        </p>
        <div className="engine-grid">
          {(pp?.engines ?? []).map((e) => (
            <EngineCard
              key={e.id}
              engine={e}
              isDefault={pp?.default_engine === e.id}
              onMakeDefault={() => makeDefault(e.id)}
            />
          ))}
        </div>
      </div>

      <MotionAiPanel />

      <div className="panel panel-help">
        <div className="panel-title"><IconInfo size={16} /> About local models</div>
        <p className="help-foot mono" style={{ borderTop: 0, paddingTop: 0, marginTop: 0 }}>
          Everything here runs on your own hardware — interpolation on the machine that captured the
          timelapse, motion analysis on whichever node you point Ollama at. No cloud, no accounts.
        </p>
      </div>
    </div>
  );
}

function MotionAiPanel() {
  const ai = useAi().data;
  const update = useUpdateAi();
  const toast = useToast();
  const [model, setModel] = useState("");
  const [url, setUrl] = useState("");
  const [dirty, setDirty] = useState(false);

  // Mirror server values into the inputs unless the user is mid-edit.
  useEffect(() => {
    if (ai && !dirty) {
      setModel(ai.model);
      setUrl(ai.base_url);
    }
  }, [ai, dirty]);

  const enabled = !!ai?.enabled;

  const onToggle = async (on: boolean) => {
    try {
      await update.mutateAsync({ enabled: on });
      toast.ok(on ? "AI motion analysis on" : "AI motion analysis off");
    } catch {
      toast.err("Could not update AI");
    }
  };

  const onSave = async () => {
    try {
      await update.mutateAsync({ model: model.trim() || undefined, base_url: url.trim() || undefined });
      setDirty(false);
      toast.ok("Saved — re-checking Ollama");
    } catch {
      toast.err("Could not save");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">
        <IconChip size={16} /> Motion AI (Ollama)
        <span style={{ flex: 1 }} />
        <Toggle checked={enabled} label="Enable AI motion analysis" onChange={onToggle} />
      </div>
      <p className="engine-intro">
        When on, motion events from cameras with motion detection enabled get labeled by a local
        Ollama vision model (person / animal / vehicle…). Point the URL at whichever node runs Ollama
        (e.g. your Mac mini). Nothing leaves your tailnet.
      </p>

      {enabled && ai && (
        <div className="kv">
          <span className="kv-k">Ollama</span>
          <span className="kv-v">
            {ai.reachable && ai.model_present ? (
              <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> Ready</span>
            ) : ai.reachable ? (
              <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> Model not pulled</span>
            ) : (
              <span className="badge badge-err"><span className="pill-dot" style={{ background: "var(--err)" }} /> Unreachable</span>
            )}
          </span>
        </div>
      )}

      <div className="ai-form">
        <label className="tl-field">
          <span className="microlabel">Model</span>
          <input
            className="tl-input"
            value={model}
            placeholder="moondream"
            onChange={(e) => { setModel(e.target.value); setDirty(true); }}
          />
        </label>
        <label className="tl-field">
          <span className="microlabel">Ollama URL</span>
          <input
            className="tl-input"
            value={url}
            placeholder="http://localhost:11434"
            onChange={(e) => { setUrl(e.target.value); setDirty(true); }}
          />
        </label>
        <div className="tl-field tl-field-action">
          <Button variant="primary" disabled={!dirty || update.isPending} onClick={onSave}>
            Save &amp; test
          </Button>
        </div>
      </div>

      {enabled && ai?.reachable && !ai.model_present && (
        <span className="help-foot mono">Model not pulled — run on the Ollama host: ollama pull {ai.model}</span>
      )}
    </div>
  );
}
