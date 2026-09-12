import { useState } from "react";

import { useHosts, useNodeCapabilities, useProbeNodeCapabilities, useSystem } from "../api/hooks";
import { IconChip, IconRefresh } from "../icons";
import { fmtBytes, fmtDateTime } from "../lib/format";
import type { ReadinessState } from "../types";
import { Button } from "./ui";

const STATUS: Record<ReadinessState, { label: string; className: string }> = {
  ready: { label: "Ready", className: "badge-ok" },
  unavailable: { label: "Unavailable", className: "badge-err" },
  disabled: { label: "Disabled", className: "readiness-muted" },
  unchecked: { label: "Not checked", className: "badge-warn" },
};

function CheckTime({ timestamp }: { timestamp: number }) {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return <>Not checked yet</>;
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return <>Not checked yet</>;
  return <time dateTime={date.toISOString()}>{fmtDateTime(timestamp)}</time>;
}

function ReadinessSnapshot({ nodeKey }: { nodeKey: string }) {
  const query = useNodeCapabilities(nodeKey);
  const probe = useProbeNodeCapabilities();
  const data = query.data;
  const readiness = data?.readiness;
  const stale = Boolean(readiness && (query.isError || probe.isError));

  if (!data) {
    if (query.isPending) return <p className="ais-intro" role="status">Loading readiness…</p>;
    return (
      <div className="readiness-message">
        <p role="alert">Could not load this node’s readiness. Check its connection and try again.</p>
        <Button onClick={() => query.refetch()} disabled={query.isFetching}>
          {query.isFetching ? "Retrying…" : "Retry readiness"}
        </Button>
      </div>
    );
  }

  if (!readiness) {
    return <p className="ais-intro">Readiness is not reported by this node. Update it to inspect its runtime checks.</p>;
  }

  const capacity = readiness.capacity;
  const error = probe.isError ? probe.error : query.error;
  return (
    <>
      <div className="readiness-actions">
        {readiness.probe_supported && (
          <Button variant="outline" icon={<IconRefresh size={15} className={probe.isPending ? "spin" : ""} />}
            disabled={probe.isPending} onClick={() => probe.mutate(nodeKey)}>
            {probe.isPending ? "Checking runtimes…" : probe.isError ? "Retry runtime check" : "Check runtimes"}
          </Button>
        )}
        {query.isError && !probe.isError && (
          <Button disabled={query.isFetching || probe.isPending} onClick={() => query.refetch()}>Retry readiness</Button>
        )}
        <span className="readiness-snapshot-time">Snapshot collected: <CheckTime timestamp={readiness.checked_at} /></span>
      </div>
      {probe.isPending && <p className="readiness-probing" role="status">Checking runtimes on the selected node. Models are not loaded or downloaded.</p>}
      <div className="readiness-capacity" aria-label="Node capacity">
        <div><span className="microlabel">CPU</span><strong>{capacity.cpu_count > 0 ? `${capacity.cpu_count} logical CPUs` : "Not reported"}</strong></div>
        <div><span className="microlabel">RAM</span><strong>{capacity.total_ram_bytes > 0 ? fmtBytes(capacity.total_ram_bytes) : "Not reported"}</strong></div>
        <div>
          <span className="microlabel">Media space</span>
          <strong>{capacity.media_free_bytes == null ? "Free space unknown" : `${fmtBytes(capacity.media_free_bytes)} free`}</strong>
          {capacity.media_total_bytes != null && <span>of {fmtBytes(capacity.media_total_bytes)}</span>}
          <span>{capacity.media_writable == null ? "Write access unknown" : capacity.media_writable ? "Writable" : "Not writable"}</span>
        </div>
      </div>

      {stale && (
        <div className="readiness-stale" role="alert">
          <strong>Stale snapshot</strong>
          <p>The latest {probe.isError ? "runtime check" : "refresh"} failed. Showing the last received results.</p>
          {error instanceof Error && <p>{error.message}</p>}
        </div>
      )}

      <ul className="readiness-tasks" aria-label="Task readiness">
        {readiness.tasks.map((task) => {
          const status = STATUS[task.state] ?? STATUS.unchecked;
          return (
            <li key={task.id} className="readiness-task">
              <div className="readiness-task-head">
                <h3>{task.label}</h3>
                <span className={`badge ${status.className}`}>{status.label}</span>
              </div>
              <p>{task.detail}</p>
              <div className="readiness-task-time">{task.checked_at > 0 && "Checked: "}<CheckTime timestamp={task.checked_at} /></div>
            </li>
          );
        })}
      </ul>
      {readiness.tasks.length === 0 && <p className="ais-intro">No task readiness checks were reported.</p>}
    </>
  );
}

export function NodeReadinessPanel() {
  const [nodeKey, setNodeKey] = useState("local");
  const hosts = useHosts().data ?? [];
  const system = useSystem().data;
  const local = hosts.find((host) => host.node_key === "local");
  const peers = hosts.filter((host) => host.node_key !== "local");
  const missingPeer = nodeKey !== "local" && !peers.some((host) => host.node_key === nodeKey);

  return (
    <section className="panel node-readiness" aria-labelledby="node-readiness-title">
      <h2 className="panel-title" id="node-readiness-title"><IconChip size={16} /> Runtime readiness</h2>
      <p className="ais-intro">
        Inspect current task checks and what needs attention. Results are diagnostics, not a reservation or a performance guarantee.
      </p>
      <label className="tl-field readiness-node-select">
        <span className="microlabel">Check device</span>
        <select className="tl-select" value={nodeKey} onChange={(event) => setNodeKey(event.target.value)}>
          <option value="local">{local?.node_name || system?.node_name || local?.host || system?.host || "This device"} (this device)</option>
          {peers.map((host) => (
            <option key={host.node_key} value={host.node_key}>{host.node_name || host.host}{host.online ? "" : " · offline"}</option>
          ))}
          {missingPeer && <option value={nodeKey}>{nodeKey} · not currently discovered</option>}
        </select>
      </label>
      {/* A new selection has its own query/error state. Late probe responses
          remain associated with their original node in the query cache. */}
      <ReadinessSnapshot key={nodeKey} nodeKey={nodeKey} />
    </section>
  );
}
