import { useState } from "react";
import { useDatasets, useModels } from "../api/hooks";
import { useCreateSupervision, useDatasetRevision } from "../api/workloadHooks";
import type { DatasetRevision, SupervisionRecord, WorkloadWorker } from "../workloadTypes";
import { defaultWorkloadBudget, WorkloadBudgetEditor, workloadBudgetError } from "./WorkloadBudgetEditor";
import { workloadDuration, workloadError, WorkloadNotice } from "./WorkloadCommon";
import { Button } from "./ui";

export function SupervisionCreateForm({ workers, onCreated }: { workers: WorkloadWorker[]; onCreated: (record: SupervisionRecord) => void }) {
  const datasets = useDatasets(); const models = useModels();
  const create = useCreateSupervision();
  const [datasetId, setDatasetId] = useState(0);
  const revision = useDatasetRevision(datasetId);
  const [review, setReview] = useState<DatasetRevision | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [cameras, setCameras] = useState<string[]>([]); const [classes, setClasses] = useState<string[]>([]);
  const [name, setName] = useState(""); const [evaluation, setEvaluation] = useState("");
  const [metric, setMetric] = useState(""); const [threshold, setThreshold] = useState("");
  const [direction, setDirection] = useState<"maximize" | "minimize">("maximize");
  const [modelIds, setModelIds] = useState<number[]>([]); const [workerIds, setWorkerIds] = useState<string[]>([]);
  const [epochsMin, setEpochsMin] = useState(1); const [epochsMax, setEpochsMax] = useState(10);
  const [imageMin, setImageMin] = useState(224); const [imageMax, setImageMax] = useState(640);
  const [seedsText, setSeedsText] = useState("0"); const [experiments, setExperiments] = useState(3);
  const [totalWall, setTotalWall] = useState(10800); const [attempts, setAttempts] = useState(1);
  const [agentTimeout, setAgentTimeout] = useState(300); const [budget, setBudget] = useState(defaultWorkloadBudget);
  const [acknowledged, setAcknowledged] = useState(false);
  const seeds = [...new Set(seedsText.split(",").map(value => Number(value.trim())))];
  const rangesValid = [epochsMin, epochsMax, imageMin, imageMax].every(value => Number.isSafeInteger(value) && value >= 1 && value <= 65536) && epochsMin <= epochsMax && imageMin <= imageMax && imageMin >= 32;
  const missing = !review || !name.trim() || !evaluation.trim() || !metric.trim() || !threshold.trim() || !cameras.length || !classes.length || !modelIds.length || !workerIds.length;
  const limitsError = workloadBudgetError(budget) || (!rangesValid ? "Use ordered, positive epoch and image-size ranges." : !seedsText.trim() || seeds.some(seed => !Number.isSafeInteger(seed) || seed < 0 || seed > 2 ** 31 - 1) || seeds.length > 32 ? "Use up to 32 comma-separated integer seeds." : !Number.isSafeInteger(experiments) || experiments < 1 || experiments > 100 ? "Approve between 1 and 100 experiments." : !Number.isFinite(totalWall) || totalWall < budget.wall_seconds || totalWall > 604800 ? "The total time budget must cover an experiment and be no longer than 7 days." : !Number.isSafeInteger(attempts) || attempts < 1 || attempts > 3 ? "Allow between 1 and 3 attempts per experiment." : !Number.isSafeInteger(agentTimeout) || agentTimeout < 30 || agentTimeout > 3600 ? "Agent timeout must be between 30 and 3600 seconds." : null);
  const changed = !!review && !!revision.data && review.revision !== revision.data.revision;
  const clearApproval = () => setAcknowledged(false);
  return <div className="panel"><h2 className="panel-title">Approve a bounded training objective</h2>
    <p className="workload-help">Approval creates an immutable experiment policy. It does not start training. An operator or authenticated external supervisor submits each permitted experiment separately; model activation is excluded.</p>
    {(datasets.isError || models.isError) && <WorkloadNotice error>Datasets or registered models could not be loaded. <Button onClick={() => { datasets.refetch(); models.refetch(); }}>Retry approval inputs</Button></WorkloadNotice>}
    <fieldset className="workload-fieldset" disabled={create.isPending} onChange={clearApproval}><legend>Objective and reviewed data</legend>
      <div className="workload-form-grid">
        <label className="tl-field"><span className="microlabel">Objective name</span><input className="tl-input" aria-label="Training objective name" maxLength={160} value={name} onChange={event => setName(event.target.value)} /></label>
        <label className="tl-field"><span className="microlabel">Dataset on this node</span><select className="tl-select" aria-label="Supervisor dataset" value={datasetId} onChange={event => { setDatasetId(Number(event.target.value)); setReview(null); setCameras([]); setClasses([]); }}><option value={0}>Choose reviewed training data</option>{(datasets.data ?? []).map(dataset => <option value={dataset.id} key={dataset.id}>{dataset.name} · {dataset.sample_count} samples</option>)}</select></label>
      </div>
      <div className="workload-actions"><Button variant="outline" disabled={!datasetId || revision.isFetching || reviewing} onClick={async () => {
        setReviewing(true); clearApproval();
        try { const result = await revision.refetch(); if (!result.isError && result.data) { setReview(structuredClone(result.data)); setCameras([...result.data.camera_ids]); setClasses([...result.data.classes]); create.reset(); } }
        finally { setReviewing(false); }
      }}>{reviewing ? "Reviewing dataset…" : "Review current dataset revision"}</Button></div>
      {revision.isError && <WorkloadNotice error>{workloadError(revision.error)}</WorkloadNotice>}
      {review && <>
        <p className="workload-help">Reviewed {review.task} dataset #{review.dataset_id}. Select the permitted cameras and classes; a changed dataset must be reviewed again.</p>
        <details className="workload-identities"><summary>Reviewed dataset SHA-256</summary><p className="mono workload-wrap">{review.revision}</p></details>
        <div className="workload-form-grid"><fieldset className="workload-fieldset"><legend>Permitted cameras</legend>{review.camera_ids.map(camera => <label className="workload-check" key={camera}><input type="checkbox" checked={cameras.includes(camera)} onChange={event => setCameras(current => event.target.checked ? [...current, camera] : current.filter(item => item !== camera))} /><span>{camera}</span></label>)}{!review.camera_ids.length && <p className="workload-help">No camera scope reported; this dataset cannot be approved yet.</p>}</fieldset>
          <fieldset className="workload-fieldset"><legend>Permitted classes</legend>{review.classes.map(label => <label className="workload-check" key={label}><input type="checkbox" checked={classes.includes(label)} onChange={event => setClasses(current => event.target.checked ? [...current, label] : current.filter(item => item !== label))} /><span>{label}</span></label>)}</fieldset></div>
      </>}
      <div className="workload-form-grid">
        <label className="tl-field"><span className="microlabel">Evaluation reference</span><input className="tl-input" aria-label="Evaluation reference" maxLength={256} value={evaluation} onChange={event => setEvaluation(event.target.value)} placeholder="Reviewed split or evaluation record" /></label>
        <label className="tl-field"><span className="microlabel">Success metric</span><input className="tl-input" aria-label="Success metric" maxLength={128} value={metric} onChange={event => setMetric(event.target.value)} placeholder="Metric reported by the selected backend" /></label>
        <label className="tl-field"><span className="microlabel">Metric direction</span><select className="tl-select" aria-label="Metric direction" value={direction} onChange={event => setDirection(event.target.value as "maximize" | "minimize")}><option value="maximize">Higher is better</option><option value="minimize">Lower is better</option></select></label>
        <label className="tl-field"><span className="microlabel">Target metric value</span><input className="tl-input" aria-label="Target metric value" type="number" step="any" value={threshold} onChange={event => setThreshold(event.target.value)} /></label>
      </div><p className="workload-help">The evaluation reference is recorded for provenance. This initial Supervisor compares reported training metrics; it does not execute a held-out evaluation or promote a model.</p>
    </fieldset>
    <fieldset className="workload-fieldset" disabled={create.isPending} onChange={clearApproval}><legend>Approved models and workers</legend><div className="workload-form-grid">
      <div><b>Registered models</b>{(models.data ?? []).map(model => <label className="workload-check" key={model.id}><input type="checkbox" disabled={!model.has_artifact} checked={modelIds.includes(model.id)} onChange={event => setModelIds(current => event.target.checked ? [...current, model.id] : current.filter(id => id !== model.id))} /><span>{model.name} · #{model.id}{model.has_artifact ? "" : " · artifact not available"}</span></label>)}{!models.data?.length && <p className="workload-help">Register provisioned model weights in AI Studio first.</p>}</div>
      <div><b>Allowed workers</b>{workers.map(worker => <label className="workload-check" key={worker.node_id}><input type="checkbox" disabled={!worker.roles.includes("training")} checked={workerIds.includes(worker.node_id)} onChange={event => setWorkerIds(current => event.target.checked ? [...current, worker.node_id] : current.filter(id => id !== worker.node_id))} /><span>{worker.name || worker.node_id} · {worker.online ? worker.tasks.find(task => task.task === "training")?.state ?? "not reported" : "offline"}</span></label>)}{!workers.length && <p className="workload-help">No approved worker identities are reported.</p>}</div>
    </div><p className="workload-help">Admission rechecks each worker's runtime and budget before starting an experiment.</p></fieldset>
    <fieldset className="workload-fieldset" disabled={create.isPending} onChange={clearApproval}><legend>Experiment limits</legend><div className="workload-form-grid">
      {[
        ["Minimum epochs", epochsMin, setEpochsMin, 1, 65536], ["Maximum epochs", epochsMax, setEpochsMax, 1, 65536],
        ["Minimum image size", imageMin, setImageMin, 32, 65536], ["Maximum image size", imageMax, setImageMax, 32, 65536],
        ["Maximum experiments", experiments, setExperiments, 1, 100], ["Total wall budget (seconds)", totalWall, setTotalWall, 1, 604800],
        ["Maximum attempts per experiment", attempts, setAttempts, 1, 3], ["Agent heartbeat timeout (seconds)", agentTimeout, setAgentTimeout, 30, 3600],
      ].map(([label, value, setter, minimum, maximum]) => <label className="tl-field" key={String(label)}><span className="microlabel">{String(label)}</span><input className="tl-input" aria-label={String(label)} type="number" min={Number(minimum)} max={Number(maximum)} step={1} value={Number(value)} onChange={event => (setter as (value: number) => void)(Number(event.target.value))} /></label>)}
      <label className="tl-field"><span className="microlabel">Allowed seeds</span><input className="tl-input" aria-label="Allowed experiment seeds" value={seedsText} onChange={event => setSeedsText(event.target.value)} placeholder="0, 1, 2" /></label>
    </div><WorkloadBudgetEditor value={budget} onChange={value => { setBudget(value); clearApproval(); }} label="Per-experiment resource limits" />
    <p className="workload-help">Up to {experiments} experiments, {workloadDuration(totalWall)} total reserved wall time. Reserved budgets are not refunded after failures or reconnects.</p></fieldset>
    {changed && <WorkloadNotice error>The dataset changed after review. Review the current revision before approving.</WorkloadNotice>}
    {limitsError && <WorkloadNotice error>{limitsError}</WorkloadNotice>}
    {create.isError && <WorkloadNotice error>{workloadError(create.error)} Your draft is preserved. Refresh the supervision list if approval could not be confirmed.</WorkloadNotice>}
    <label className="workload-check"><input type="checkbox" checked={acknowledged} disabled={create.isPending || reviewing || revision.isFetching} onChange={event => setAcknowledged(event.target.checked)} /><span>I approve this dataset revision, scope and finite experiment budget. Model activation is not permitted.</span></label>
    <div className="workload-actions"><Button variant="primary" disabled={missing || !!limitsError || changed || !Number.isFinite(Number(threshold)) || !acknowledged || create.isPending || reviewing || revision.isFetching} onClick={async () => {
      if (!review) return;
      try { const result = await create.mutateAsync({ objective: { name: name.trim(), task: review.task, dataset_id: review.dataset_id, dataset_revision: review.revision, camera_ids: cameras, classes, success_criteria: [{ metric: metric.trim(), direction, threshold: Number(threshold) }], evaluation_reference: evaluation.trim() }, policy: { allowed_model_ids: modelIds, allowed_worker_node_ids: workerIds, epochs: { minimum: epochsMin, maximum: epochsMax }, image_size: { minimum: imageMin, maximum: imageMax }, allowed_seeds: seeds, max_experiments: experiments, total_wall_seconds: totalWall, experiment_budget: budget, max_attempts: attempts, agent_timeout_seconds: agentTimeout, permitted_actions: ["experiment", "inspect", "stop", "finish"], activation_enabled: false } }); onCreated(result); } catch { /* Explicit approval can be reviewed again; never auto-start. */ }
    }}>Approve experiment policy</Button></div>
  </div>;
}
