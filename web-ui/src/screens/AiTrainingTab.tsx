import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { sampleThumbUrl } from "../api/client";
import { AnnotationEditor } from "../components/AnnotationEditor";
import {
  useActivateModel,
  useCameras,
  useCreateDataset,
  useDatasets,
  useDeleteDataset,
  useDeleteSample,
  useImportEvents,
  useRelabelSample,
  useRuns,
  useSamples,
  useStartRun,
  useStopRun,
  useTraining,
  useUpdateCollection,
} from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, Toggle } from "../components/ui";
import { IconBolt, IconBrain, IconCheck, IconChip, IconMotion, IconStop, IconTrash } from "../icons";
import type { DatasetInfo, DatasetTask, TrainingRunInfo } from "../types";

/** The training workflow, in the order you actually do it:
 * 1 Collect frames → 2 Label them → 3 Train → 4 Use the model. */
export function AiTrainingTab() {
  const training = useTraining().data;
  const cameras = useCameras().data ?? [];
  const datasets = useDatasets().data ?? [];
  const runs = useRuns().data ?? [];
  const [params] = useSearchParams();

  const onlineCams = cameras.filter((c) => c.status !== "offline").length;
  const classes = training?.classes ?? [];
  const totalSamples = training?.total_samples ?? 0;
  const labeled = datasets.some(
    (d) =>
      d.task === "detection"
        ? d.annotated_count > 0
        : Object.keys(d.label_counts).filter((k) => k !== "__unlabeled__").length >= 2,
  );
  const hasComplete = runs.some((r) => r.status === "complete");
  const usingTrained = !!training?.active_model_id;

  const steps = [
    { n: 1, label: "Collect", done: totalSamples > 0 },
    { n: 2, label: "Label", done: labeled },
    { n: 3, label: "Train", done: hasComplete },
    { n: 4, label: "Use it", done: usingTrained },
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
        <span className="ais-flow-meta mono">{totalSamples} samples collected</span>
      </div>

      <CollectionPanel onlineCams={onlineCams} datasets={datasets} />
      <DatasetsPanel
        datasets={datasets}
        classes={classes}
        activeId={training?.active_dataset_id ?? 0}
        initialDataset={Number(params.get("dataset")) || 0}
      />
      <TrainPanel datasets={datasets} engineOk={!!training?.engine_available} />
      <RunsPanel datasets={datasets} />
    </>
  );
}

function labeledClassCount(d: DatasetInfo | undefined): number {
  if (!d) return 0;
  return Object.keys(d.label_counts).filter((k) => k !== "__unlabeled__").length;
}

// -------------------------------------------------------------- 1. Collect
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
        <IconBrain size={16} /> 1 · Collect from cameras
        <span style={{ flex: 1 }} />
        <Toggle
          checked={collecting}
          label="Collect training data from all cameras"
          onChange={(on) => set({ enabled: on }, on ? "Collecting from your cameras" : "Collection stopped")}
        />
      </div>
      <p className="engine-intro">
        When on, a frame from every online camera ({onlineCams}) is added to the active dataset on an
        interval. Frames are weak-labeled by your Ollama model when auto-label is on — correct them in
        step 2.
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

// ---------------------------------------------------------- 2. Datasets/label
function DatasetsPanel({
  datasets, classes, activeId, initialDataset,
}: { datasets: DatasetInfo[]; classes: string[]; activeId: number; initialDataset?: number }) {
  const create = useCreateDataset();
  const del = useDeleteDataset();
  const importEvents = useImportEvents();
  const toast = useToast();
  const [name, setName] = useState("");
  const [task, setTask] = useState<DatasetTask>("classification");
  const [selected, setSelected] = useState<number | null>(initialDataset || null);
  const [confirm, setConfirm] = useState<DatasetInfo | null>(null);

  useEffect(() => {
    if (selected == null && datasets.length) setSelected(activeId || datasets[0].id);
  }, [datasets, activeId, selected]);

  const onCreate = async () => {
    if (!name.trim()) return;
    try {
      const d = await create.mutateAsync({ name: name.trim(), task });
      setName("");
      setSelected(d.id);
      toast.ok("Dataset created");
    } catch {
      toast.err("Could not create dataset");
    }
  };

  const selectedDs = datasets.find((d) => d.id === selected);

  return (
    <div className="panel">
      <div className="panel-title"><IconChip size={16} /> 2 · Datasets &amp; labeling</div>
      <div className="ai-form" style={{ gridTemplateColumns: "1fr 1fr auto" }}>
        <label className="tl-field">
          <span className="microlabel">New dataset name</span>
          <input className="tl-input" value={name} placeholder="e.g. Driveway, Workshop"
            onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onCreate()} />
        </label>
        <label className="tl-field">
          <span className="microlabel">Type</span>
          <select className="tl-select" value={task} onChange={(e) => setTask(e.target.value as DatasetTask)}>
            <option value="classification">Classification (whole-frame label)</option>
            <option value="detection">Detection (bounding boxes)</option>
          </select>
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
                <span className="ds-name">
                  {d.name}
                  <span className={`badge ${d.task === "detection" ? "badge-accent" : "badge-ok"} ds-task`}>
                    {d.task === "detection" ? "detection" : "classification"}
                  </span>
                  {d.id === activeId && <span className="badge badge-accent ds-active">collecting → here</span>}
                </span>
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

      {selected != null && selectedDs && (
        <SampleGrid
          datasetId={selected}
          task={selectedDs.task}
          classes={classes}
          onImportEvents={async () => {
            try {
              await importEvents.mutateAsync(selected);
              toast.ok("Imported motion events (already-imported ones are skipped)");
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
  datasetId, task, classes, onImportEvents, importing,
}: {
  datasetId: number;
  task: DatasetTask;
  classes: string[];
  onImportEvents: () => void;
  importing: boolean;
}) {
  const [filter, setFilter] = useState("");
  const samples = useSamples(datasetId, filter || undefined).data ?? [];
  const relabel = useRelabelSample();
  const del = useDeleteSample();
  const detection = task === "detection";
  const [annotating, setAnnotating] = useState<number | null>(null);

  return (
    <div className="sample-block">
      <div className="sample-bar">
        <div className="filter-scroll">
          <button className={`chip-filter ${!filter ? "is-on" : ""}`} onClick={() => setFilter("")}>All</button>
          <button className={`chip-filter ${filter === "__unlabeled__" ? "is-on" : ""}`} onClick={() => setFilter("__unlabeled__")}>Unlabeled</button>
          {!detection && classes.map((c) => (
            <button key={c} className={`chip-filter ${filter === c ? "is-on" : ""}`} onClick={() => setFilter(c)}>{c}</button>
          ))}
        </div>
        <Button variant="outline" size="sm" icon={<IconMotion size={14} />} disabled={importing} onClick={onImportEvents}>
          Import motion events
        </Button>
      </div>

      {detection && (
        <p className="help-foot mono" style={{ marginTop: 0 }}>
          Detection dataset — open a sample to draw bounding boxes (where + what). Train once a few
          samples are annotated.
        </p>
      )}

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
                {detection && s.annotation_count > 0 && (
                  <span className="sample-boxes">{s.annotation_count} box{s.annotation_count === 1 ? "" : "es"}</span>
                )}
              </div>
              {detection ? (
                <button
                  className={`sample-label sample-annotate ${s.annotation_count ? "" : "is-empty"}`}
                  onClick={() => setAnnotating(s.id)}
                >
                  {s.annotation_count ? `Edit ${s.annotation_count} box${s.annotation_count === 1 ? "" : "es"}` : "Annotate"}
                </button>
              ) : (
                <select
                  className={`sample-label ${s.label ? "" : "is-empty"}`}
                  value={s.label ?? ""}
                  onChange={(e) => relabel.mutate({ id: s.id, label: e.target.value || null })}
                >
                  <option value="">— unlabeled —</option>
                  {classes.map((c) => <option key={c} value={c}>{c}</option>)}
                  {s.label && !classes.includes(s.label) && <option value={s.label}>{s.label}</option>}
                </select>
              )}
            </div>
          ))}
        </div>
      )}

      {annotating != null && (
        <AnnotationEditor
          sampleId={annotating}
          classes={classes}
          onClose={() => setAnnotating(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 3. Train
function TrainPanel({ datasets, engineOk }: { datasets: DatasetInfo[]; engineOk: boolean }) {
  const t = useTraining().data;
  const start = useStartRun();
  const toast = useToast();
  const [did, setDid] = useState(0);
  const [epochs, setEpochs] = useState(30);

  useEffect(() => {
    if (!did && datasets.length) setDid(datasets[0].id);
  }, [datasets, did]);

  const ds = datasets.find((d) => d.id === did);
  const detection = ds?.task === "detection";
  const classes = labeledClassCount(ds);
  const ready = detection ? (ds?.annotated_count ?? 0) >= 1 : classes >= 2;
  const canTrain = engineOk && ready && !start.isPending;

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
      <div className="panel-title">
        <IconBolt size={16} /> 3 · Train a model
        <span style={{ flex: 1 }} />
        {engineOk ? (
          <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> Engine ready · {t?.device?.toUpperCase()}</span>
        ) : (
          <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> Engine not installed</span>
        )}
      </div>
      <p className="engine-intro">
        Fine-tune a model on a labeled dataset. When done it appears under <b>Models → Your models</b> —
        activate it there to analyze your cameras with your own model.
      </p>
      <div className="ai-form" style={{ gridTemplateColumns: "1.4fr 1fr auto" }}>
        <label className="tl-field">
          <span className="microlabel">Dataset</span>
          <select className="tl-select" value={did} onChange={(e) => setDid(Number(e.target.value))}>
            {datasets.length === 0 && <option value={0}>— no datasets —</option>}
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.task === "detection"
                  ? `${d.name} · ${d.annotated_count} annotated`
                  : `${d.name} · ${labeledClassCount(d)} labeled classes`}
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
        <span className="help-foot mono">
          Training runs on your Mac/Windows GPU — install the optional engine there:{" "}
          <span className="lit">pip install "tailcam[training]"</span>. Collection and labeling work without it.
        </span>
      ) : !ready ? (
        <span className="help-foot mono">
          {detection
            ? "Annotate at least one sample with a bounding box (above) before training."
            : "Label samples in at least 2 classes (above) before training."}
        </span>
      ) : null}
    </div>
  );
}

// ----------------------------------------------------------------- 4. Runs
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
      <div className="panel-title"><IconBolt size={16} /> 4 · Training runs</div>
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
                {typeof r.metrics.map50 === "number" ? ` · mAP50 ${(r.metrics.map50 * 100).toFixed(1)}%` : ""}
                {r.log ? ` · ${r.log}` : ""}
              </div>
            </div>
          );
        })}
      </div>
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
