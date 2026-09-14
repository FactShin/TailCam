import { useState } from "react";
import { useHosts, useNodeCapabilities } from "../api/hooks";
import { artifactContentUrl } from "../api/storage";
import { useArtifacts, useRetryStorageTransfer, useStorageTransfers, useUpdateArtifactRetention } from "../api/storageHooks";
import { fmtBytes, fmtDateTime } from "../lib/format";
import { CONTENT_KINDS, type ArtifactFilters, type StorageArtifact } from "../storageTypes";
import { kindLabel, NumberField, storageError, StorageNotice } from "./StoragePolicyPanel";
import { Button } from "./ui";

function ArtifactRetention({artifact}: {artifact: StorageArtifact}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({...artifact.retention});
  const save = useUpdateArtifactRetention();
  if (!open) return <Button size="sm" variant="outline" onClick={() => {setDraft({...artifact.retention}); save.reset(); setOpen(true);}}>Edit artifact retention</Button>;
  return <form className="storage-section" aria-label="Artifact retention" onSubmit={async e => {
    e.preventDefault();
    try {const result = await save.mutateAsync({id: artifact.artifact_id, retention: draft}); setDraft({...result.retention});} catch { /* Preserve rejected edits. */ }
  }}>
    <fieldset className="storage-fieldset" disabled={save.isPending}>
      <label className="storage-check"><input type="checkbox" checked={draft.protect} onChange={e => {setDraft(d => ({...d, protect: e.target.checked})); save.reset();}} /> Protect this artifact from deletion</label>
      <label className="storage-check"><input type="checkbox" checked={draft.enabled} onChange={e => {setDraft(d => ({...d, enabled: e.target.checked, max_age_seconds: e.target.checked && d.max_age_seconds === 0 ? 30 * 86400 : d.max_age_seconds})); save.reset();}} /> Enable expiry for this artifact</label>
      {draft.enabled && <NumberField label="Artifact expiry age (days)" min={1} value={draft.max_age_seconds / 86400} step="any" onChange={v => {setDraft(d => ({...d, max_age_seconds: Math.round(v * 86400)})); save.reset();}} />}
      <NumberField label="Artifact minimum verified replicas" min={1} max={10} value={draft.min_replicas} onChange={v => {setDraft(d => ({...d, min_replicas: v})); save.reset();}} />
      <p className="stor-note">Expiry is measured from creation. This changes only this artifact's deletion policy; it does not move content or create replicas.</p>
      {save.isError && <StorageNotice error>{storageError(save.error)}</StorageNotice>}{save.isSuccess && <StorageNotice>Artifact retention saved.</StorageNotice>}
      <div className="storage-actions"><Button type="submit" variant="primary">{save.isPending ? "Saving…" : "Save artifact retention"}</Button><Button type="button" onClick={() => setOpen(false)}>Close retention</Button></div>
    </fieldset>
  </form>;
}

export function StorageContent() {
  const [filters, setFilters] = useState<ArtifactFilters>({});
  const [camera, setCamera] = useState("");
  const q = useArtifacts(filters);
  const hosts = useHosts().data ?? [];
  const localId = hosts.find(h => h.kind === "local")?.node_id;
  const principal = useNodeCapabilities("local").data?.principal;
  const admin = principal?.verified === true && principal.roles.includes("admin");
  const rows = q.data?.pages.flatMap(p => p.items) ?? [];
  const name = (id: string) => {const h = hosts.find(h => h.node_id === id); return h?.node_name || h?.host || id;};
  return <section className="panel" aria-label="Content catalog">
    <div className="panel-title">Content catalog</div>
    <p className="ais-intro">The recorded owner identifies where bytes are stored. Offline entries remain visible with their last reported state.</p>
    <form className="storage-filters" onSubmit={e => {e.preventDefault(); setFilters(f => ({...f, camera_id: camera || undefined}));}}>
      <label className="tl-field"><span className="microlabel">Origin device</span><select aria-label="Origin device" className="tl-select" value={filters.origin_node_id ?? ""} onChange={e => setFilters(f => ({...f, origin_node_id: e.target.value || undefined}))}><option value="">All origins</option>{hosts.filter(h => h.node_id).map(h => <option key={h.node_key} value={h.node_id!}>{h.node_name || h.host}</option>)}</select></label>
      <label className="tl-field"><span className="microlabel">Content type</span><select aria-label="Content type" className="tl-select" value={filters.kind ?? ""} onChange={e => setFilters(f => ({...f, kind: e.target.value || undefined}))}><option value="">All content</option>{CONTENT_KINDS.map(k => <option key={k} value={k}>{kindLabel(k)}</option>)}</select></label>
      <label className="tl-field"><span className="microlabel">Content state</span><select aria-label="Content state" className="tl-select" value={filters.state ?? ""} onChange={e => setFilters(f => ({...f, state: e.target.value || undefined}))}><option value="">All states</option><option value="pending_transfer">Pending transfer</option><option value="committed">Committed</option><option value="replicated">Replicated</option><option value="failed">Failed</option><option value="deleted">Deleted</option></select></label>
      <label className="tl-field"><span className="microlabel">Catalog camera ID</span><input className="tl-input" maxLength={256} value={camera} onChange={e => setCamera(e.target.value)} /></label><Button type="submit" variant="outline">Filter camera</Button>
    </form>
    {q.isPending && <StorageNotice>Loading content…</StorageNotice>}
    {q.isError && <StorageNotice error>{q.data ? "The displayed catalog may be stale. " : ""}{storageError(q.error)} <Button onClick={() => q.refetch()}>Retry content</Button></StorageNotice>}
    {!q.isPending && !q.isError && !rows.length && <p className="storage-empty">No catalog entries match these filters. Older media remains in Gallery until it is indexed.</p>}
    <div className="storage-list">{rows.map(a => <article key={a.artifact_id} className="storage-item">
      <div className="storage-row-head"><h3 className="storage-heading">{typeof a.metadata.name === "string" ? a.metadata.name : kindLabel(a.kind)}</h3><span className={`badge ${a.state === "committed" || a.state === "replicated" ? "badge-ok" : "badge-warn"}`}>{a.state.split("_").join(" ")}</span></div>
      <p className="storage-meta">{kindLabel(a.kind)} · {fmtBytes(a.size_bytes)} · {fmtDateTime(a.created_at)}</p>
      <dl className="storage-stats"><div><dt>Origin / camera</dt><dd>{name(a.origin_node_id)}{a.camera_id ? ` / ${a.camera_id}` : ""}</dd></div><div><dt>Current owner</dt><dd>{name(a.owner_node_id)} · {a.owner_online === true ? "online" : a.owner_online === false ? "offline" : "availability not reported"}</dd></div><div><dt>Last reported</dt><dd>{a.last_seen === null ? "Not reported" : fmtDateTime(a.last_seen)}</dd></div><div><dt>Verified replicas</dt><dd>{a.replicas.length}</dd></div></dl>
      <p className="storage-id mono">Artifact: {a.artifact_id}<br />Location: {a.location_id || "not committed"}</p>
      {a.parent_id && <p className="storage-id mono">Related to: {a.parent_id}</p>}
      <p className="storage-meta">{a.retention.protect ? "Protected from deletion" : "Not protected"} · {a.retention.enabled ? `expires after ${a.retention.max_age_seconds / 86400} days` : "expiry disabled"} · minimum {a.retention.min_replicas} verified {a.retention.min_replicas === 1 ? "replica" : "replicas"}</p>
      {a.owner_online === true && (a.state === "committed" || a.state === "replicated")
        ? <a className="btn btn-outline btn-sm" href={artifactContentUrl(a.artifact_id)} target="_blank" rel="noreferrer">Open content</a>
        : <p className="stor-note">{a.owner_online === false ? "The owner is offline. Content will be available when it reconnects." : a.state === "pending_transfer" ? "Content is waiting for a verified destination copy." : "Content availability has not been confirmed."}</p>}
      {a.state !== "deleted" && (a.owner_node_id === localId && admin ? <ArtifactRetention artifact={a} /> : <p className="stor-note">{a.owner_node_id !== localId ? "Edit this artifact's retention on its current owner device." : "An administrator connection is required to edit artifact retention."}</p>)}
    </article>)}</div>
    {q.hasNextPage && <div className="storage-actions"><Button variant="outline" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Loading…" : "Load more content"}</Button></div>}
  </section>;
}

export function StorageTransfers() {
  const q = useStorageTransfers();
  const retry = useRetryStorageTransfer();
  const rows = q.data?.pages.flatMap(p => p.items) ?? [];
  return <section className="panel" aria-label="Transfer status">
    <div className="panel-title">Transfers on this device</div>
    <p className="ais-intro">Receiving bytes and verifying them are separate steps. Content is committed only after verification succeeds.</p>
    {q.isPending && <StorageNotice>Loading transfers…</StorageNotice>}
    {q.isError && <StorageNotice error>{q.data ? "Transfer status may be stale. " : ""}{storageError(q.error)} <Button onClick={() => q.refetch()}>Retry transfer status</Button></StorageNotice>}
    {!q.isPending && !q.isError && rows.length === 0 && <p className="storage-empty">No transfers reported on this device.</p>}
    {retry.isError && <StorageNotice error>{storageError(retry.error)}</StorageNotice>}
    {retry.isSuccess && <StorageNotice>{retry.data > 0 ? "Transfer verified at destination." : "Retry finished without a new commitment. Review the updated transfer state."}</StorageNotice>}
    <div className="storage-list">{rows.map(t => <article className="storage-item" key={t.transfer_id}>
      <div className="storage-row-head"><h3 className="storage-heading">{t.state === "committed" ? "Verified and committed" : t.state === "verifying" ? "Verifying content" : t.state === "receiving" ? t.direction === "outbound" ? "Waiting for destination commitment" : "Receiving content" : t.state === "failed" ? "Transfer failed" : "Transfer cancelled"}</h3><span className={`badge ${t.state === "committed" ? "badge-ok" : "badge-warn"}`}>{t.state}</span></div>
      <progress className="storage-progress" max={Math.max(t.size_bytes, 1)} value={Math.min(t.offset, t.size_bytes)} aria-label={`Bytes received for ${t.artifact_id}`} />
      <p className="storage-meta">{fmtBytes(t.offset)} of {fmtBytes(t.size_bytes)} · updated {fmtDateTime(t.updated_at)}</p>
      <p className="storage-id mono">Artifact: {t.artifact_id}<br />Location: {t.location_id}</p>
      {t.direction && <p className="storage-meta">{t.direction === "outbound" ? "Outbound from this device" : "Received by this device"}</p>}
      {t.requested_destination && <p className="storage-id mono">Requested destination: {t.requested_destination.node_id} / {t.requested_destination.location_id || "device default"}<br />Current owner: {t.actual_owner_node_id || "not reported"}</p>}
      {t.error_code && <StorageNotice error>Transfer stopped: {t.error_code}. The source copy is retained until a destination verifies it.</StorageNotice>}
      {t.direction === "outbound" && t.state === "failed" && <Button variant="outline" disabled={retry.isPending} onClick={() => retry.mutate(t.transfer_id)}>{retry.isPending && retry.variables === t.transfer_id ? "Retrying…" : "Retry transfer"}</Button>}
    </article>)}</div>
    {q.hasNextPage && <Button variant="outline" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Loading…" : "Load more transfers"}</Button>}
  </section>;
}
