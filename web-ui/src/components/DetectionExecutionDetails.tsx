import { useDetections, useHosts } from "../api/hooks";
import { workloadNode, workloadTime } from "./WorkloadCommon";

const elapsed = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) && value >= 0
  ? `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ms`
  : "Not reported";

/** Observe LiveViewer's cache without adding another polling or inference request. */
export function DetectionExecutionDetails({ prefix, cameraId, polling }: { prefix: string; cameraId: string; polling: boolean }) {
  const result = useDetections(prefix, cameraId, false);
  const hosts = useHosts().data ?? [];
  const observation = result.data?.workload;
  return <details className="detection-execution">
    <summary>Detection execution details</summary>
    <p className="workload-help">Latest result received by this viewer. Opening these details does not request another inference. Timings describe this result only.</p>
    {!result.data ? <p className="workload-help">No detection result received yet. Enable the detection overlay on an online, unzoomed camera to receive results.</p> : <>
      {result.isError && <p className="workload-help" role="status">Detection refresh failed. These are the last reported observations and may be stale.</p>}
      {!polling && <p className="workload-help">The detection overlay is paused or off. These observations are from the last received result.</p>}
      <dl className="workload-facts">
        <div><dt>Model used</dt><dd>{observation?.model_name || result.data.model_name || "Not reported"}</dd></div>
        <div><dt>Result received</dt><dd>{workloadTime(result.dataUpdatedAt / 1000)}</dd></div>
        <div><dt>Actual worker</dt><dd>{observation?.worker_node_id ? workloadNode(observation.worker_node_id, hosts) : "Not reported"}</dd></div>
        <div><dt>Queue time</dt><dd>{elapsed(observation?.queue_ms)}</dd></div>
        <div><dt>Execution time</dt><dd>{elapsed(observation?.execution_ms)}</dd></div>
        <div><dt>Round-trip time</dt><dd>{elapsed(observation?.round_trip_ms)}</dd></div>
      </dl>
      {!observation && <p className="workload-help">This node did not report worker or timing observations.</p>}
      {observation && <details className="workload-identities"><summary>Worker and session identity</summary><dl className="workload-facts"><div><dt>Worker UUID</dt><dd className="mono">{observation.worker_node_id || "Not reported"}</dd></div><div><dt>Session</dt><dd className="mono">{observation.session_id || "Not reported"}</dd></div></dl></details>}
    </>}
  </details>;
}
