import { useEffect, useState } from "react";

import { sampleThumbUrl } from "../api/client";
import {
  useActivateModel,
  useCameras,
  useCreateDataset,
  useDatasets,
  useDeactivateModel,
  useDeleteDataset,
  useDeleteModel,
  useDeleteSample,
  useImportEvents,
  useModels,
  useRegisterModel,
  useRelabelSample,
  useRuns,
  useSamples,
  useStartRun,
  useStopRun,
  useTraining,
  useUpdateCollection,
} from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, Spinner, Toggle } from "../components/ui";
import { IconBolt, IconBrain, IconChip, IconInfo, IconMotion, IconStop, IconTrash } from "../icons";
import { fmtAgo } from "../lib/format";
import type { DatasetInfo, ModelInfo, TrainingRunInfo } from "../types";

export function Training() {
  const training = useTraining().data;
  const cameras = useCameras().data ?? [];
  const datasets = useDatasets().data ?? [];

  const onlineCams = cameras.filter((c) => c.status !== "offline").length;
  const classes = training?.classes ?? [];

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Train your own model</span></div>
          <h1 className="screen-title">Training</h1>
          <p className="screen-sub">
            Build a private camera-analysis model from your own footage — {training?.total_samples ?? 0} sample
            {(training?.total_samples ?? 0) !== 1 ? "s" : ""} collected
          </p>
        </div>
      </div>

      <EnginePanel />
      <CollectionPanel onlineCams={onlineCams} datasets={datasets} />
      <DatasetsPanel datasets={datasets} classes={classes} activeId={training?.active_dataset_id ?? 0} />
      <TrainPanel datasets={datasets} engineOk={!!training?.engine_available} />
      <RunsPanel datasets={datasets} />
      <ModelsPanel activeModelId={training?.active_model_id ?? 0} />
    </div>
  );
}

function labeledClassCount(d: DatasetInfo | undefined): number {
  if (!d) return 0;
  return Object.keys(d.label_counts).filter((k) => k !== "__unlabeled__").length;
}

function TrainPanel({ datasets, engineOk }: { datasets: DatasetInfo[]; engineOk: boolean }) {
  const start = useStartRun();
  const toast = useToast();
  const [did, setDid] = useState(0);
  const [epochs, setEpochs] = useState(30);

  useEffect(() => {
    if (!did && datasets.length) setDid(datasets[0].id);
  }, [datasets, did]);

  const ds = datasets.find((d) => d.id === did);
  const classes = labeledClassCount(ds);
  const canTrain = engineOk && classes >= 2 && !start.isPending;

  const onTrain = async () => {
    try {
      await start.mutateAsync({ dataset_id: did, epochs });
      toast.ok("Training started");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not start training");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title"><IconBolt size={16} /> Train a model</div>
      <p className="engine-intro">
        Fine-tune a model on a labeled dataset. It trains on this machine's GPU and, when done, is
        added to your registry below — activate it to analyze your cameras with your own model.
      </p>
      <div className="ai-form" style={{ gridTemplateColumns: "1.4fr 1fr auto" }}>
        <label className="tl-field">
          <span className="microlabel">Dataset</span>
          <select className="tl-select" value={did} onChange={(e) => setDid(Number(e.target.value))}>
            {datasets.length === 0 && <option value={0}>— no datasets —</option>}
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} · {labeledClassCount(d)} labeled classes
              </option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span className="microlabel">Epochs</span>
          <input className="tl-input" type="number" min={1} max={300} value={epochs}
            onChange={(e) => setEpochs(Math.max(1, Math.min(300, Number(e.target.value) || 1)))} />
        </label>
        <div className="tl-field tl-field-action">
          <Button variant="primary" icon={<IconBrain size={15} />} disabled={!canTrain} onClick={onTrain}>
            Train
          </Button>
        </div>
      </div>
      {!engineOk ? (
        <span className="help-foot mono">Install the training engine on this machine to enable training.</span>
      ) : classes < 2 ? (
        <span className="help-foot mono">Label samples in at least 2 classes (above) before training.</span>
      ) : null}
    </div>
  );
}

const RUN_BADGE: Record<string, string> = {
  queued: "badge-warn",
  preparing: "badge-warn",
  training: "badge-ok",
  complete: "badge-ok",
  error: "badge-err",
  stopped: "badge-warn",
};

function RunsPanel({ datasets }: { datasets: DatasetInfo[] }) {
  const runs = useRuns().data ?? [];
  const stop = useStopRun();
  const activate = useActivateModel();
  const toast = useToast();
  if (runs.length === 0) return null;

  const dsName = (id: number) => datasets.find((d) => d.id === id)?.name ?? `#${id}`;
  const active = (s: string) => s === "queued" || s === "preparing" || s === "training";

  return (
    <div className="panel">
      <div className="panel-title"><IconBolt size={16} /> Training runs</div>
      <div className="model-list">
        {runs.map((r: TrainingRunInfo) => {
          const pct = r.epochs ? Math.round((r.epoch / r.epochs) * 100) : 0;
          return (
            <div key={r.id} className="run-row">
              <div className="run-head">
                <span className={`badge ${RUN_BADGE[r.status] ?? "badge-warn"}`}>{r.status}</span>
                <span className="run-name">{dsName(r.dataset_id)} · {r.base_model}</span>
                <span className="grow" />
                {active(r.status) && (
                  <Button variant="ghost" size="sm" icon={<IconStop size={13} />} onClick={() => stop.mutate(r.id)}>Stop</Button>
                )}
                {r.status === "complete" && r.model_id && (
                  <Button variant="outline" size="sm" onClick={() => { activate.mutate(r.model_id as number); toast.ok("Model activated"); }}>
                    Use model
                  </Button>
                )}
              </div>
              {active(r.status) && (
                <div className="run-bar"><span className="run-bar-fill" style={{ width: `${pct}%` }} /></div>
              )}
              <div className="run-meta mono">
                {r.status === "training" ? `epoch ${r.epoch}/${r.epochs}` : `${r.epochs} epochs`}
                {typeof r.metrics.top1 === "number" ? ` · top-1 ${(r.metrics.top1 * 100).toFixed(1)}%` : ""}
                {r.log ? ` · ${r.log}` : ""}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EnginePanel() {
  const t = useTraining().data;
  const ok = !!t?.engine_available;
  return (
    <div className="panel">
      <div className="panel-title">
        <IconBrain size={16} /> Training engine
        <span style={{ flex: 1 }} />
        {ok ? (
          <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> Ready · {t?.device}</span>
        ) : (
          <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> Not installed</span>
        )}
      </div>
      {ok ? (
        <div className="kv"><span className="kv-k">Framework</span><span className="kv-v mono">{t?.framework}{t?.version ? ` ${t.version}` : ""} · {t?.device?.toUpperCase()}</span></div>
      ) : (
        <p className="engine-hint mono">
          Training runs on your Mac/Windows GPU. Install the optional engine there:{" "}
          <span className="lit">pip install "tailcam[training]"</span> (PyTorch + Ultralytics). Data
          collection and labeling work without it — you just can't run a training pass yet.
        </p>
      )}
    </div>
  );
}

function CollectionPanel({ onlineCams, datasets }: { onlineCams: number; datasets: DatasetInfo[] }) {
  const t = useTraining().data;
  const update = useUpdateCollection();
  const toast = useToast();
  const collecting = !!t?.collecting;

  const set = async (body: Parameters<typeof update.mutateAsync>[0], msg?: string) => {
    try {
      await update.mutateAsync(body);
      if (msg) toast.ok(msg);
    } catch {
      toast.err("Could not update collection");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">
        <IconBrain size={16} /> Collect from cameras
        <span style={{ flex: 1 }} />
        <Toggle
          checked={collecting}
          label="Collect training data from all cameras"
          onChange={(on) => set({ enabled: on }, on ? "Collecting from your cameras" : "Collection stopped")}
        />
      </div>
      <p className="engine-intro">
        When on, a frame from every online camera ({onlineCams}) is added to the active dataset on an
        interval — so all your feeds train the model. Frames are weak-labeled by your Ollama model when
        auto-label is on, and you can correct them below.
      </p>
      <div className="ai-form">
        <label className="tl-field">
          <span className="microlabel">Active dataset</span>
          <select
            className="tl-select"
            value={t?.active_dataset_id ?? 0}
            onChange={(e) => set({ active_dataset_id: Number(e.target.value) })}
          >
            {datasets.length === 0 && <option value={0}>— create one below —</option>}
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>{d.name} ({d.sample_count})</option>
            ))}
          </select>
        </label>
        <label className="tl-field">
          <span className="microlabel">Interval (s)</span>
          <input
            className="tl-input"
            type="number"
            min={2}
            step={5}
            defaultValue={t?.collect_interval_seconds ?? 30}
            onBlur={(e) => set({ interval_seconds: Math.max(2, Number(e.target.value) || 30) })}
          />
        </label>
        <label className="tl-field tl-field-action" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <Toggle checked={!!t?.auto_label} label="Auto-label" onChange={(on) => set({ auto_label: on })} />
          <span className="microlabel">Auto-label (Ollama)</span>
        </label>
      </div>
      {collecting && (
        <span className="help-foot mono">
          ● Collecting · {t?.collected_session ?? 0} frame{(t?.collected_session ?? 0) !== 1 ? "s" : ""} this session
        </span>
      )}
    </div>
  );
}

function DatasetsPanel({ datasets, classes, activeId }: { datasets: DatasetInfo[]; classes: string[]; activeId: number }) {
  const create = useCreateDataset();
  const del = useDeleteDataset();
  const importEvents = useImportEvents();
  const toast = useToast();
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<DatasetInfo | null>(null);

  useEffect(() => {
    if (selected == null && datasets.length) setSelected(activeId || datasets[0].id);
  }, [datasets, activeId, selected]);

  const onCreate = async () => {
    if (!name.trim()) return;
    try {
      const d = await create.mutateAsync({ name: name.trim() });
      setName("");
      setSelected(d.id);
      toast.ok("Dataset created");
    } catch {
      toast.err("Could not create dataset");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title"><IconChip size={16} /> Datasets</div>
      <div className="ai-form" style={{ gridTemplateColumns: "1fr auto" }}>
        <label className="tl-field">
          <span className="microlabel">New dataset name</span>
          <input className="tl-input" value={name} placeholder="e.g. Driveway, Workshop"
            onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onCreate()} />
        </label>
        <div className="tl-field tl-field-action">
          <Button variant="primary" disabled={!name.trim() || create.isPending} onClick={onCreate}>Create</Button>
        </div>
      </div>

      {datasets.length === 0 ? (
        <p className="help-foot mono">No datasets yet — create one, then turn on collection above.</p>
      ) : (
        <div className="ds-list">
          {datasets.map((d) => (
            <button
              key={d.id}
              className={`ds-row ${selected === d.id ? "is-sel" : ""}`}
              onClick={() => setSelected(d.id)}
            >
              <div className="ds-row-main">
                <span className="ds-name">{d.name}{d.id === activeId && <span className="badge badge-accent ds-active">collecting → here</span>}</span>
                <span className="ds-meta mono">{d.sample_count} samples · {labelSummary(d)}</span>
              </div>
              <span
                className="ds-del"
                role="button"
                aria-label="Delete dataset"
                onClick={(e) => { e.stopPropagation(); setConfirm(d); }}
              >
                <IconTrash size={15} />
              </span>
            </button>
          ))}
        </div>
      )}

      {selected != null && datasets.some((d) => d.id === selected) && (
        <SampleGrid
          datasetId={selected}
          classes={classes}
          onImportEvents={async () => {
            try {
              await importEvents.mutateAsync(selected);
              toast.ok("Imported motion events");
            } catch {
              toast.err("Import failed");
            }
          }}
          importing={importEvents.isPending}
        />
      )}

      <ConfirmDialog
        open={!!confirm}
        title="Delete dataset?"
        danger
        confirmLabel="Delete"
        body={confirm ? `"${confirm.name}" and its ${confirm.sample_count} sample(s) will be permanently removed.` : ""}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          if (!confirm) return;
          const id = confirm.id;
          setConfirm(null);
          try { await del.mutateAsync(id); if (selected === id) setSelected(null); toast.ok("Deleted"); }
          catch { toast.err("Delete failed"); }
        }}
      />
    </div>
  );
}

function SampleGrid({
  datasetId,
  classes,
  onImportEvents,
  importing,
}: {
  datasetId: number;
  classes: string[];
  onImportEvents: () => void;
  importing: boolean;
}) {
  const [filter, setFilter] = useState("");
  const samples = useSamples(datasetId, filter || undefined).data ?? [];
  const relabel = useRelabelSample();
  const del = useDeleteSample();

  return (
    <div className="sample-block">
      <div className="sample-bar">
        <div className="filter-scroll">
          <button className={`chip-filter ${!filter ? "is-on" : ""}`} onClick={() => setFilter("")}>All</button>
          <button className={`chip-filter ${filter === "__unlabeled__" ? "is-on" : ""}`} onClick={() => setFilter("__unlabeled__")}>Unlabeled</button>
          {classes.map((c) => (
            <button key={c} className={`chip-filter ${filter === c ? "is-on" : ""}`} onClick={() => setFilter(c)}>{c}</button>
          ))}
        </div>
        <Button variant="outline" size="sm" icon={<IconMotion size={14} />} disabled={importing} onClick={onImportEvents}>
          Import motion events
        </Button>
      </div>

      {samples.length === 0 ? (
        <p className="help-foot mono">No samples here yet. Turn on collection, or import motion events.</p>
      ) : (
        <div className="sample-grid">
          {samples.map((s) => (
            <div key={s.id} className="sample-card">
              <div className="sample-thumb">
                <img src={sampleThumbUrl(s.id)} alt={s.label ?? "unlabeled"} loading="lazy" />
                <button className="sample-del" aria-label="Delete sample" onClick={() => del.mutate(s.id)}>
                  <IconTrash size={13} />
                </button>
                {s.source === "motion" && <span className="sample-src">motion</span>}
              </div>
              <select
                className={`sample-label ${s.label ? "" : "is-empty"}`}
                value={s.label ?? ""}
                onChange={(e) => relabel.mutate({ id: s.id, label: e.target.value || null })}
              >
                <option value="">— unlabeled —</option>
                {classes.map((c) => <option key={c} value={c}>{c}</option>)}
                {s.label && !classes.includes(s.label) && <option value={s.label}>{s.label}</option>}
              </select>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelsPanel({ activeModelId }: { activeModelId: number }) {
  const models = useModels().data ?? [];
  const activate = useActivateModel();
  const deactivate = useDeactivateModel();
  const register = useRegisterModel();
  const del = useDeleteModel();
  const toast = useToast();
  const [byoName, setByoName] = useState("");
  const [byoPath, setByoPath] = useState("");
  const [confirm, setConfirm] = useState<ModelInfo | null>(null);

  const onRegister = async () => {
    if (!byoPath.trim()) return;
    try {
      await register.mutateAsync({ name: byoName.trim() || "My model", path: byoPath.trim() });
      setByoName(""); setByoPath("");
      toast.ok("Model added");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not add model");
    }
  };

  return (
    <div className="panel">
      <div className="panel-title"><IconChip size={16} /> Models
        <span style={{ flex: 1 }} />
        <span className="kv-v mono" style={{ fontSize: 11, color: "var(--muted)" }}>
          Active: {activeModelId ? (models.find((m) => m.id === activeModelId)?.name ?? `#${activeModelId}`) : "Default analyzer (Ollama)"}
        </span>
      </div>
      <p className="engine-intro">
        Use <b>our base model</b>, a model you’ve <b>trained</b>, or <b>bring your own</b> <span className="mono">.pt</span> file.
        The active model is what analyzes your cameras.
      </p>

      <div className="model-list">
        {models.map((m) => (
          <div key={m.id} className={`model-row ${m.active ? "is-active" : ""}`}>
            <div className="model-main">
              <span className="model-name">{m.name}</span>
              <span className="model-meta mono">
                <span className={`badge ${m.kind === "base" ? "badge-accent" : m.kind === "trained" ? "badge-ok" : "badge-warn"}`}>{m.kind}</span>
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
        <div className="tl-field tl-field-action">
          <Button variant="primary" disabled={!byoPath.trim() || register.isPending} onClick={onRegister}>Add</Button>
        </div>
      </div>

      <div className="kv kv-stack" style={{ marginTop: 12 }}>
        <span className="help-foot mono" style={{ borderTop: 0, paddingTop: 0, margin: 0 }}>
          <IconInfo size={12} /> Training a model from your datasets runs in the next update — collect &amp;
          label footage now and it’s ready to train.
        </span>
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

function labelSummary(d: DatasetInfo): string {
  const entries = Object.entries(d.label_counts);
  if (entries.length === 0) return "empty";
  return entries
    .map(([k, v]) => `${k === "__unlabeled__" ? "unlabeled" : k}: ${v}`)
    .join(" · ");
}
