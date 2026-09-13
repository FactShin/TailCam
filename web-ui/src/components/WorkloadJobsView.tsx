import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useHosts, useNodeCapabilities } from "../api/hooks";
import { useControlWorkloadJob, useWorkloadJob, useWorkloadJobs } from "../api/workloadHooks";
import { TASK_LABELS, WORKLOAD_TASKS, type JobFilters, type JobState, type WorkloadJob, type WorkloadTask } from "../workloadTypes";
import { Button } from "./ui";
import { workloadError, WorkloadNotice } from "./WorkloadCommon";
import { WorkloadJobs } from "./WorkloadJobs";

export function WorkloadJobsView() {
  const [filters, setFilters] = useState<JobFilters>({});
  const [params, setParams] = useSearchParams();
  const selected = params.get("job") ?? "";
  const jobs = useWorkloadJobs(filters, !selected);
  const job = useWorkloadJob(selected);
  const active = selected ? job : jobs;
  const shown = selected ? job.data ? [job.data] : undefined : jobs.data?.pages.flatMap(page => page.items);
  const hosts = useHosts().data ?? [];
  const access = useNodeCapabilities("local");
  const principal = access.data?.principal;
  const canOperate = !!principal?.verified && principal.roles.some(role => role === "operator" || role === "admin");
  const action = useControlWorkloadJob();
  const [notice, setNotice] = useState("");
  const perform = async (job: WorkloadJob, operation: "cancel" | "retry") => {
    setNotice("");
    try {
      const updated = await action.mutateAsync({ id: job.job_id, action: operation });
      setNotice(operation === "retry" ? "Permitted work was queued for retry." : updated.state === "cancelled" ? "The job has stopped." : "Stop requested. Waiting for the worker to confirm.");
    } catch { /* The mutation retains the safe API error below. */ }
  };
  return <div className="workload-stack">
    <div className="panel">
      <div className="workload-heading"><h2 className="panel-title">{selected ? "Selected worker job" : "Jobs coordinated by this node"}</h2><Button variant="outline" disabled={active.isFetching} onClick={() => active.refetch()}>Refresh jobs</Button></div>
      <p className="workload-help">Track the selected worker, each stage, and committed outputs. A stop request remains pending until execution confirms it stopped.</p>
      {selected && <Button variant="outline" onClick={() => { const next = new URLSearchParams(params); next.delete("job"); setParams(next); }}>Show all jobs</Button>}
      {!selected && <div className="workload-form-grid">
        <label className="tl-field"><span className="microlabel">Task</span><select className="tl-select" aria-label="Filter jobs by task" value={filters.task ?? ""} onChange={event => setFilters(current => ({ ...current, task: event.target.value as WorkloadTask || undefined }))}>
          <option value="">All tasks</option>{WORKLOAD_TASKS.map(task => <option key={task} value={task}>{TASK_LABELS[task]}</option>)}
        </select></label>
        <label className="tl-field"><span className="microlabel">State</span><select className="tl-select" aria-label="Filter jobs by state" value={filters.state ?? ""} onChange={event => setFilters(current => ({ ...current, state: event.target.value as JobState || undefined }))}>
          <option value="">All states</option>{["queued", "leased", "running", "committing", "retry_wait", "waiting_for_worker", "cancel_requested", "cancelled", "succeeded", "failed", "deadline_exceeded"].map(state => <option value={state} key={state}>{state.replace(/_/g, " ")}</option>)}
        </select></label>
      </div>}
      {!canOperate && <p className="workload-help">{access.isPending ? "Checking control permissions…" : "Operator permissions are required to stop or retry jobs."}</p>}
    </div>
    {active.isPending && <WorkloadNotice>Loading jobs…</WorkloadNotice>}
    {active.isError && <WorkloadNotice error>{shown ? "The displayed jobs may be stale. " : ""}{workloadError(active.error)} <Button variant="outline" onClick={() => active.refetch()}>Retry loading jobs</Button></WorkloadNotice>}
    {action.isError && <WorkloadNotice error>{workloadError(action.error)}</WorkloadNotice>}
    {notice && <WorkloadNotice>{notice}</WorkloadNotice>}
    {shown && <WorkloadJobs jobs={shown} hosts={hosts} canOperate={canOperate} canRetryTraining={!!principal?.verified && principal.roles.includes("admin")} busyId={action.isPending ? action.variables.id : null} onCancel={job => perform(job, "cancel")} onRetry={job => perform(job, "retry")} />}
    {!selected && jobs.hasNextPage && <Button variant="outline" disabled={jobs.isFetchingNextPage} onClick={() => jobs.fetchNextPage()}>Load more jobs</Button>}
  </div>;
}
