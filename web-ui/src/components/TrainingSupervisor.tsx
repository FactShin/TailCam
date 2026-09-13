import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useHosts, useModels, useNodeCapabilities } from "../api/hooks";
import { useControlSupervision, useControlWorkloadJob, useSubmitExperiment, useSupervision, useSupervisionReport, useSupervisions, useWorkloadWorkers } from "../api/workloadHooks";
import { fmtBytes } from "../lib/format";
import type { HostInfo, ModelInfo } from "../types";
import type { ExperimentRequest, SupervisionRecord, SupervisionReport, WorkloadWorker } from "../workloadTypes";
import { SupervisionCreateForm } from "./SupervisionCreateForm";
import { WorkloadJobs } from "./WorkloadJobs";
import { workloadDuration, workloadError, workloadNode, workloadTime, WorkloadNotice } from "./WorkloadCommon";
import { Button } from "./ui";

export function TrainingSupervisor() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("supervision") ?? "";
  const records = useSupervisions(); const record = useSupervision(selected);
  const workers = useWorkloadWorkers(); const hosts = useHosts().data ?? [];
  const models = useModels(); const principal = useNodeCapabilities("local").data?.principal;
  const admin = !!principal?.verified && principal.roles.includes("admin");
  const operator = !!principal?.verified && principal.roles.some(role => role === "admin" || role === "operator");
  const [creating, setCreating] = useState(false);
  const choose = (id: string) => { const next = new URLSearchParams(params); next.set("tab", "supervisor"); if (id) next.set("supervision", id); else next.delete("supervision"); setParams(next); setCreating(false); };
  return <div className="workload-stack">
    <div className="panel"><div className="workload-heading"><h2 className="panel-title">Training Supervisor</h2><div className="workload-actions"><Button variant="outline" disabled={records.isFetching} onClick={() => { records.refetch(); if (selected) record.refetch(); }}>Refresh supervision</Button>{admin && <Button variant="primary" onClick={() => setCreating(value => !value)}>{creating ? "Close approval form" : "Approve a new objective"}</Button>}</div></div>
      <p className="workload-help">Approve finite experiments on reviewed data, follow the worker, and compare candidates. A connected external agent may manage the approved sequence. This page polls state and does not impersonate that agent or activate models.</p>
      {!admin && <p className="workload-help">An administrator must approve a new objective and its policy.</p>}
      <a href="/ai?tab=training">Open datasets and individual training runs</a>
    </div>
    {workers.isError && <WorkloadNotice error>{workloadError(workers.error)} <Button onClick={() => workers.refetch()}>Retry workers</Button></WorkloadNotice>}
    {creating && admin && <SupervisionCreateForm workers={workers.data?.items ?? []} onCreated={created => choose(created.supervision_id)} />}
    {records.isPending && <WorkloadNotice>Loading approved objectives…</WorkloadNotice>}
    {records.isError && <WorkloadNotice error>{records.data ? "The objective list may be stale. " : ""}{workloadError(records.error)} <Button onClick={() => records.refetch()}>Retry objectives</Button></WorkloadNotice>}
    {records.data && <div className="panel"><label className="tl-field"><span className="microlabel">Approved objective</span><select className="tl-select" aria-label="Approved training objective" value={selected} onChange={event => choose(event.target.value)}><option value="">Choose an objective to inspect</option>{records.data.pages.flatMap(page => page.items).map(item => <option key={item.supervision_id} value={item.supervision_id}>{item.objective.name} · {item.state.replace(/_/g, " ")}</option>)}{selected && !records.data.pages.some(page => page.items.some(item => item.supervision_id === selected)) && <option value={selected}>{record.data?.objective.name ?? selected}</option>}</select></label>
      {!records.data.pages.some(page => page.items.length) && <p className="workload-help">No approved objectives yet. Approval defines the limits; experiments start only after a separate submission.</p>}
      {records.hasNextPage && <Button disabled={records.isFetchingNextPage} onClick={() => records.fetchNextPage()}>Load more objectives</Button>}
    </div>}
    {selected && record.isPending && <WorkloadNotice>Loading supervision…</WorkloadNotice>}
    {selected && record.isError && <WorkloadNotice error>{record.data ? "The displayed supervision may be stale. " : ""}{workloadError(record.error)} <Button onClick={() => record.refetch()}>Retry supervision</Button></WorkloadNotice>}
    {record.data && selected && <SupervisionDetail key={record.data.supervision_id} record={record.data} workers={workers.data?.items ?? []} hosts={hosts} models={models.data ?? []} operator={operator} />}
  </div>;
}

function SupervisionDetail({ record, workers, hosts, models, operator }: { record: SupervisionRecord; workers: WorkloadWorker[]; hosts: HostInfo[]; models: ModelInfo[]; operator: boolean }) {
  const control = useControlSupervision(); const jobControl = useControlWorkloadJob();
  const [finishReason, setFinishReason] = useState(""); const [showReport, setShowReport] = useState(false);
  const report = useSupervisionReport(record.supervision_id, showReport);
  const modelName = (id: number) => models.find(model => model.id === id)?.name ?? `Registered model #${id}`;
  const runControl = async (action: "stop" | "finish") => { try { await control.mutateAsync({ id: record.supervision_id, action, reason: action === "finish" ? finishReason.trim() : undefined }); } catch { /* State and safe error remain visible. */ } };
  return <>
    <div className="panel"><div className="workload-heading"><h2 className="panel-title">{record.objective.name}</h2><span className={`badge ${record.state === "completed" ? "badge-ok" : record.state === "failed" ? "badge-err" : "badge-warn"}`}>{record.state.replace(/_/g, " ")}</span></div>
      <p className="workload-help">{record.reason}</p>
      <dl className="workload-facts"><div><dt>External supervisor</dt><dd>{record.agent_connected ? "Connected" : "Disconnected"}</dd></div><div><dt>Agent heartbeat</dt><dd>{workloadTime(record.agent_heartbeat_at)}</dd></div><div><dt>Last decision</dt><dd>{record.last_decision}</dd></div><div><dt>Next planned check</dt><dd>{workloadTime(record.next_check_at)}</dd></div><div><dt>Experiments remaining</dt><dd>{record.remaining_experiments} of {record.policy.max_experiments}</dd></div><div><dt>Reserved time remaining</dt><dd>{workloadDuration(record.remaining_wall_seconds)}</dd></div><div><dt>Current job</dt><dd>{record.current_job_id ? <a href={`/workloads?tab=jobs&job=${encodeURIComponent(record.current_job_id)}`}>Inspect current worker job</a> : "No active experiment"}</dd></div><div><dt>Model activation</dt><dd>Not permitted</dd></div></dl>
      {!record.agent_connected && <WorkloadNotice>No external supervisor heartbeat is current. Existing worker progress is shown separately below; refreshing this page does not create an agent heartbeat.</WorkloadNotice>}
      {record.state === "stop_requested" && <WorkloadNotice>Stop requested. Wait for the worker's terminal state before treating execution as stopped.</WorkloadNotice>}
      <details className="workload-identities"><summary>Approved scope, policy and identity</summary><dl className="workload-facts">
        <div><dt>Supervision</dt><dd className="mono">{record.supervision_id}</dd></div><div><dt>Approved by</dt><dd>{record.owner}</dd></div><div><dt>Owning node</dt><dd>{workloadNode(record.node_id, hosts)}</dd></div><div><dt>Dataset</dt><dd>#{record.objective.dataset_id} · {record.objective.task}</dd></div><div><dt>Dataset SHA-256</dt><dd className="mono">{record.objective.dataset_revision}</dd></div><div><dt>Permitted cameras</dt><dd>{record.objective.camera_ids.join(", ")}</dd></div><div><dt>Permitted classes</dt><dd>{record.objective.classes.join(", ")}</dd></div><div><dt>Evaluation reference</dt><dd>{record.objective.evaluation_reference}</dd></div><div><dt>Registered models</dt><dd>{record.policy.allowed_model_ids.map(modelName).join(", ")}</dd></div><div><dt>Allowed workers</dt><dd>{record.policy.allowed_worker_node_ids.map(id => workloadNode(id, hosts)).join(", ")}</dd></div><div><dt>Epoch range</dt><dd>{record.policy.epochs.minimum}–{record.policy.epochs.maximum}</dd></div><div><dt>Image-size range</dt><dd>{record.policy.image_size.minimum}–{record.policy.image_size.maximum}</dd></div><div><dt>Seeds</dt><dd>{record.policy.allowed_seeds.join(", ")}</dd></div><div><dt>Attempts per experiment</dt><dd>{record.policy.max_attempts}</dd></div><div><dt>Per-experiment memory</dt><dd>{fmtBytes(record.policy.experiment_budget.memory_bytes)}</dd></div><div><dt>Per-experiment workspace / output</dt><dd>{fmtBytes(record.policy.experiment_budget.workspace_bytes)} / {fmtBytes(record.policy.experiment_budget.output_bytes)}</dd></div>
      </dl><p className="workload-help">This approved policy is immutable. A different scope or budget needs a new objective.</p></details>
      {control.isError && <WorkloadNotice error>{workloadError(control.error)}</WorkloadNotice>}
      {operator && <div className="workload-actions">{record.allowed_actions.includes("stop") && <Button variant="outline" disabled={control.isPending} onClick={() => runControl("stop")}>Request supervision stop</Button>}{record.allowed_actions.includes("finish") && <><input className="tl-input" aria-label="Reason for finishing supervision" maxLength={1024} placeholder="Reason for finishing this objective" value={finishReason} disabled={control.isPending} onChange={event => setFinishReason(event.target.value)} /><Button variant="outline" disabled={!finishReason.trim() || control.isPending} onClick={() => runControl("finish")}>Finish supervision</Button></>}</div>}
    </div>
    {operator && record.allowed_actions.includes("experiment") && <ExperimentForm key={`${record.supervision_id}/${record.remaining_experiments}`} record={record} models={models} workers={workers} hosts={hosts} />}
    <div className="panel"><h2 className="panel-title">Experiment progress</h2><p className="workload-help">Worker heartbeats and completed metrics are separate from the external supervisor connection. Missing live metrics remain unreported.</p>
      {!record.experiments.length && <p className="workload-help">No experiments submitted for this approved objective.</p>}
      {jobControl.isError && <WorkloadNotice error>{workloadError(jobControl.error)}</WorkloadNotice>}
      {record.experiments.map(experiment => <details className="workload-experiment" key={experiment.experiment_id}><summary>{modelName(experiment.request.base_model_id)} · {experiment.request.epochs} epochs · {experiment.state.replace(/_/g, " ")}</summary>
        <p className="workload-help">{experiment.request.reason} · Seed {experiment.request.seed} · Image size {experiment.request.image_size} · Reserved {workloadDuration(experiment.reserved_wall_seconds)}</p>
        {experiment.error_code && <WorkloadNotice error>Experiment needs attention: {experiment.error_code}</WorkloadNotice>}
        {experiment.job ? <WorkloadJobs jobs={[experiment.job]} hosts={hosts} canOperate={operator} busyId={jobControl.isPending ? jobControl.variables.id : null} onCancel={job => jobControl.mutate({ id: job.job_id, action: "cancel" })} onRetry={job => jobControl.mutate({ id: job.job_id, action: "retry" })} /> : <WorkloadNotice>Worker job state has not been reported.</WorkloadNotice>}
      </details>)}
    </div>
    <div className="panel"><div className="workload-heading"><h2 className="panel-title">Evidence and comparison</h2><Button variant="outline" disabled={report.isFetching} onClick={() => { if (showReport) report.refetch(); else setShowReport(true); }}>{showReport ? "Refresh report" : "Open comparison report"}</Button></div>
      {showReport && report.isPending && <WorkloadNotice>Loading comparison report…</WorkloadNotice>}{report.isError && <WorkloadNotice error>{report.data ? "This report may be stale. " : ""}{workloadError(report.error)}</WorkloadNotice>}
      {report.data && <ComparisonReport report={report.data} hosts={hosts} models={models} />}
    </div>
  </>;
}

function ExperimentForm({ record, models, workers, hosts }: { record: SupervisionRecord; models: ModelInfo[]; workers: WorkloadWorker[]; hosts: HostInfo[] }) {
  const policy = record.policy;
  const [request, setRequest] = useState<ExperimentRequest>(() => ({ idempotency_key: crypto.randomUUID(), base_model_id: policy.allowed_model_ids[0], worker_node_id: policy.allowed_worker_node_ids[0], epochs: policy.epochs.minimum, image_size: policy.image_size.minimum, seed: policy.allowed_seeds[0], reason: "" }));
  const submit = useSubmitExperiment();
  const update = (values: Partial<ExperimentRequest>) => setRequest(current => ({ ...current, ...values, idempotency_key: crypto.randomUUID() }));
  const valid = request.reason.trim() && Number.isInteger(request.epochs) && request.epochs >= policy.epochs.minimum && request.epochs <= policy.epochs.maximum && Number.isInteger(request.image_size) && request.image_size >= policy.image_size.minimum && request.image_size <= policy.image_size.maximum;
  return <div className="panel"><h2 className="panel-title">Submit one approved experiment</h2><p className="workload-help">The server checks the reviewed dataset, allowed model and worker, and remaining budget atomically. Retrying an unconfirmed submission uses the same experiment key.</p>
    <fieldset className="workload-fieldset" disabled={submit.isPending}><legend>Experiment settings</legend><div className="workload-form-grid">
      <label className="tl-field"><span className="microlabel">Approved model</span><select className="tl-select" aria-label="Experiment model" value={request.base_model_id} onChange={event => update({ base_model_id: Number(event.target.value) })}>{policy.allowed_model_ids.map(id => <option key={id} value={id}>{models.find(model => model.id === id)?.name ?? `Registered model #${id}`}</option>)}</select></label>
      <label className="tl-field"><span className="microlabel">Approved worker</span><select className="tl-select" aria-label="Experiment worker" value={request.worker_node_id} onChange={event => update({ worker_node_id: event.target.value })}>{policy.allowed_worker_node_ids.map(id => { const worker = workers.find(item => item.node_id === id); return <option key={id} value={id}>{worker?.name || workloadNode(id, hosts)} · {worker?.online ? worker.tasks.find(task => task.task === "training")?.state ?? "not reported" : "offline / not reported"}</option>; })}</select></label>
      <label className="tl-field"><span className="microlabel">Epochs</span><input className="tl-input" aria-label="Experiment epochs" type="number" min={policy.epochs.minimum} max={policy.epochs.maximum} value={request.epochs} onChange={event => update({ epochs: Number(event.target.value) })} /></label>
      <label className="tl-field"><span className="microlabel">Image size</span><input className="tl-input" aria-label="Experiment image size" type="number" min={policy.image_size.minimum} max={policy.image_size.maximum} value={request.image_size} onChange={event => update({ image_size: Number(event.target.value) })} /></label>
      <label className="tl-field"><span className="microlabel">Seed</span><select className="tl-select" aria-label="Experiment seed" value={request.seed} onChange={event => update({ seed: Number(event.target.value) })}>{policy.allowed_seeds.map(seed => <option key={seed} value={seed}>{seed}</option>)}</select></label>
      <label className="tl-field"><span className="microlabel">Reason for this experiment</span><textarea className="tl-input" aria-label="Experiment reason" maxLength={1024} value={request.reason} onChange={event => update({ reason: event.target.value })} /></label>
    </div></fieldset>
    {submit.isError && <WorkloadNotice error>{workloadError(submit.error)} This submission key is retained for retry.</WorkloadNotice>}
    <div className="workload-actions"><Button variant="primary" disabled={!valid || submit.isPending || record.remaining_experiments < 1 || record.remaining_wall_seconds < policy.experiment_budget.wall_seconds} onClick={() => submit.mutate({ id: record.supervision_id, request: { ...request, reason: request.reason.trim() } })}>Submit approved experiment</Button></div>
  </div>;
}

function ComparisonReport({ report, hosts, models }: { report: SupervisionReport; hosts: HostInfo[]; models: ModelInfo[] }) {
  const candidate = report.comparison.find(item => item.experiment_id === report.best_candidate);
  const modelName = (id: number) => models.find(model => model.id === id)?.name ?? `Registered model #${id}`;
  return <>
    <p className="workload-help">{candidate ? `Highest-ranked reported candidate: ${modelName(candidate.parameters.base_model_id)} · ${candidate.parameters.epochs} epochs · ${candidate.parameters.image_size}px · seed ${candidate.parameters.seed}.` : report.best_candidate ? `Highest-ranked candidate details not reported (${report.best_candidate}).` : "No completed experiment has a reported comparison metric yet."} Metric: {report.comparison_metric}. No model was activated.</p>
    {!!report.comparison.length && <div className="workload-table-wrap"><table className="workload-table"><thead><tr><th>Experiment</th><th>Worker and settings</th><th>Reported metrics</th><th>State</th></tr></thead><tbody>{report.comparison.map(item => {
      const metrics = Object.entries(item.metrics).filter(([, value]) => typeof value === "number" && Number.isFinite(value));
      return <tr key={item.experiment_id}><td data-label="Experiment">{item.experiment_id === report.best_candidate && <span className="badge">Ranked candidate</span>}<span className="mono">{item.experiment_id}</span></td><td data-label="Worker and settings">{modelName(item.parameters.base_model_id)}<br />{workloadNode(item.parameters.worker_node_id, hosts)}<br />{item.parameters.epochs} epochs · {item.parameters.image_size}px · seed {item.parameters.seed}</td><td data-label="Reported metrics">{metrics.length ? metrics.map(([key, value]) => <div key={key}>{key}: {String(value)}</div>) : "Not reported"}</td><td data-label="State">{item.state.replace(/_/g, " ")}{item.error_code && <span>{item.error_code}</span>}</td></tr>;
    })}</tbody></table></div>}
    {report.comparison.map(item => <details className="workload-identities" key={item.experiment_id}><summary>Provenance and saved outputs · {item.experiment_id}</summary><p className="workload-help">Job {item.job_id} · Dataset #{item.dataset_id} · Reserved {workloadDuration(item.reserved_wall_seconds)}</p><p className="mono workload-wrap">Dataset SHA-256 {item.dataset_revision}</p>
      {!item.artifacts.length && <p className="workload-help">No committed output artifacts reported.</p>}
      {item.artifacts.map(artifact => <p className="workload-help" key={artifact.artifact_id}><a href={`/api/v1/artifacts/${encodeURIComponent(artifact.artifact_id)}/content`}>{artifact.slot}</a> · {fmtBytes(artifact.size_bytes)} · Owner {workloadNode(artifact.owner_node_id, hosts)}<br /><span className="mono workload-wrap">SHA-256 {artifact.sha256}</span></p>)}
      {item.provenance.map(stage => <div className="workload-stage" key={stage.stage_id}><b>{stage.stage_id}</b><p className="workload-help">Worker {workloadNode(stage.worker_node_id, hosts)} · Started {workloadTime(stage.started_at)} · Ended {workloadTime(stage.ended_at)}</p>{stage.placement_plan && <p className="workload-help">{stage.placement_plan.reason} · Policy revision {stage.placement_plan.policy_revision}</p>}<details><summary>Reported runtime evidence</summary><pre className="workload-evidence">{JSON.stringify(stage.result, null, 2)}</pre></details></div>)}
    </details>)}
    {!!report.limitations.length && <ul className="workload-help">{report.limitations.map(item => <li key={item}>{item}</li>)}</ul>}
  </>;
}
