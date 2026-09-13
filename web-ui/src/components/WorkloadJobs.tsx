import type { HostInfo } from "../types";
import { fmtBytes } from "../lib/format";
import { TASK_LABELS, type JobStage, type WorkloadJob } from "../workloadTypes";
import { Button, Spinner } from "./ui";
import { JobStatus, targetLabel, workloadDuration, workloadNode, workloadTime, WorkloadNotice } from "./WorkloadCommon";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function WorkloadJobs({ jobs, hosts, canOperate, canRetryTraining = false, busyId, onCancel, onRetry }: {
  jobs: WorkloadJob[]; hosts: HostInfo[]; canOperate: boolean; busyId: string | null;
  canRetryTraining?: boolean;
  onCancel: (job: WorkloadJob) => void; onRetry: (job: WorkloadJob) => void;
}) {
  if (!jobs.length) return <WorkloadNotice>No jobs match this view. New work appears here after it is submitted.</WorkloadNotice>;
  return <div className="workload-stack">{jobs.map(job => {
    const busy = busyId === job.job_id;
    const now = Date.now() / 1000;
    const queue = (job.started_at ?? job.ended_at ?? now) - job.created_at;
    const execution = job.started_at == null ? null : (job.ended_at ?? now) - job.started_at;
    return <article className="panel workload-job" key={job.job_id}>
      <div className="workload-heading"><h2 className="panel-title">{TASK_LABELS[job.task]}</h2><JobStatus state={job.state} /></div>
      <dl className="workload-facts">
        <div><dt>Requested worker</dt><dd>{targetLabel(job.requested_target, hosts)}</dd></div>
        <div><dt>Actual worker</dt><dd>{targetLabel(job.actual_target, hosts)}</dd></div>
        <div><dt>Origin</dt><dd>{workloadNode(job.origin_node_id, hosts)}</dd></div>
        <div><dt>Coordinator</dt><dd>{workloadNode(job.coordinator_node_id, hosts)}</dd></div>
        <div><dt>Queue time</dt><dd>{workloadDuration(queue)}</dd></div>
        <div><dt>Execution time</dt><dd>{workloadDuration(execution)}</dd></div>
        <div><dt>Deadline</dt><dd>{workloadTime(job.deadline_at)}</dd></div>
      </dl>
      {job.error && <WorkloadNotice error>{job.error.detail} <span className="mono">({job.error.code})</span></WorkloadNotice>}
      {(job.cancel_requested || job.state === "cancel_requested") && job.state !== "cancelled" && <WorkloadNotice>Cancellation is requested. Work has not yet confirmed it stopped.</WorkloadNotice>}
      <div className="workload-stack">{job.stages.map(stage => <Stage key={stage.stage_id} stage={stage} hosts={hosts} />)}</div>
      <details className="workload-identities"><summary>Job identity</summary><dl className="workload-facts"><div><dt>Job</dt><dd className="mono">{job.job_id}</dd></div><div><dt>Revision</dt><dd>{job.revision}</dd></div><div><dt>Updated</dt><dd>{workloadTime(job.updated_at)}</dd></div></dl></details>
      {canOperate && <div className="workload-actions">
        {job.allowed_actions.includes("cancel") && <Button variant="outline" disabled={busy || job.cancel_requested} onClick={() => onCancel(job)}>{busy ? <Spinner size={14} /> : null} Request stop</Button>}
        {job.allowed_actions.includes("retry") && !job.reference.supervision_id && (job.task !== "training" || canRetryTraining) && <Button variant="outline" disabled={busy} onClick={() => onRetry(job)}>{busy ? <Spinner size={14} /> : null} Retry permitted work</Button>}
      </div>}
    </article>;
  })}</div>;
}

function Stage({ stage, hosts }: { stage: JobStage; hosts: HostInfo[] }) {
  const progress = stage.progress;
  const metrics = Object.entries(progress.metrics).filter(([, value]) => Number.isFinite(value));
  const predictions = stage.result.predictions;
  const boxes = stage.state === "succeeded" && stage.result.available === true && stage.result.outcome === "succeeded" && Array.isArray(predictions) && predictions.length > 0 && predictions.every(item => item && typeof item === "object" && Array.isArray(item.boxes))
    ? predictions.reduce((count, item) => count + item.boxes.length, 0) as number : null;
  return <section className="workload-stage" aria-label={`Stage ${stage.stage_id}`}>
    <div className="workload-heading"><b>{TASK_LABELS[stage.task]} · {stage.stage_id}</b><JobStatus state={stage.state} /></div>
    {stage.placement_plan && <p className="workload-help">{stage.placement_plan.reason} · Policy revision {stage.placement_plan.policy_revision}</p>}
    <p className="workload-help">Worker: {workloadNode(stage.worker_node_id, hosts)} · Attempt {stage.attempt} · Last heartbeat: {workloadTime(stage.heartbeat_at)}</p>
    {progress.fraction != null && <progress className="workload-progress" aria-label={`${stage.stage_id} progress`} max={1} value={progress.fraction} />}
    {(progress.message || progress.epoch != null) && <p className="workload-help">{progress.epoch != null ? `Epoch ${progress.epoch} · ` : ""}{progress.message}</p>}
    {metrics.length ? <dl className="workload-facts">{metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : <p className="workload-help">Metrics not reported.</p>}
    {stage.error && <WorkloadNotice error>{stage.error.detail} <span className="mono">({stage.error.code})</span></WorkloadNotice>}
    {boxes != null && <p className="workload-help">{boxes === 0 ? "Inference completed successfully with no detections." : `Inference completed with ${boxes} detection${boxes === 1 ? "" : "s"}.`}</p>}
    {!!stage.outputs.length && <ul className="workload-output-list">{stage.outputs.map(output => <li key={`${output.slot}/${output.artifact_id}`}>
      {UUID.test(output.artifact_id) ? <a href={`/api/v1/artifacts/${encodeURIComponent(output.artifact_id)}/content`}>{output.slot}</a> : <span>{output.slot}</span>}
      <span>{fmtBytes(output.size_bytes)} · Owner: {workloadNode(output.owner_node_id, hosts)}</span>
      <details><summary>Artifact identity and checksum</summary><span className="mono">{output.artifact_id}</span><span className="mono">SHA-256 {output.sha256}</span></details>
    </li>)}</ul>}
  </section>;
}
