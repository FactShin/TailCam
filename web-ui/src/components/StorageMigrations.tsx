import { useEffect, useState } from "react";
import { useControlMigration, usePreviewMigration, useStartMigration, useStorageDestinations, useStorageLocations, useStorageMigrations } from "../api/storageHooks";
import { fmtBytes, fmtDateTime } from "../lib/format";
import { CONTENT_KINDS, type ContentKind, type DestinationRef } from "../storageTypes";
import { DestinationPicker, kindLabel, storageError, StorageNotice } from "./StoragePolicyPanel";
import { Button } from "./ui";

export function StorageMigrations() {
  const locations = useStorageLocations();
  const destinations = useStorageDestinations();
  const preview = usePreviewMigration();
  const start = useStartMigration();
  const jobs = useStorageMigrations();
  const control = useControlMigration();
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState<DestinationRef>({node_id: "", location_id: null});
  const [kinds, setKinds] = useState<ContentKind[]>([]);
  const [removeSource, setRemoveSource] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer);}, []);
  const resetPreview = () => {preview.reset(); start.reset(); setAcknowledged(false);};
  const expired = !!preview.data && preview.data.expires_at * 1000 <= now;
  // Resolve labels from the reviewed IDs, never from the current form selection.
  const sourceLocation = locations.data?.items.find(l => l.location_id === preview.data?.source_location_id);
  const sourceNode = destinations.data?.items.find(n => n.node_id === sourceLocation?.node_id);
  const destinationNode = destinations.data?.items.find(n => n.node_id === preview.data?.destination.node_id);
  const destinationLocation = destinationNode?.locations.find(l => l.location_id === preview.data?.destination.location_id);
  const submit = (e: React.FormEvent) => {e.preventDefault(); start.reset(); setAcknowledged(false); preview.mutate({source_location_id: source, destination, ...(kinds.length ? {content_kinds: kinds} : {}), remove_source: removeSource});};
  return <div className="storage-stack">
    <section className="panel" aria-label="Move existing content">
      <div className="panel-title">Copy or move existing content</div>
      <p className="ais-intro">Preview the exact content first. A move removes source copies only after verified destination copies and references are committed.</p>
      {(locations.isPending || destinations.isPending) && <StorageNotice>Loading migration locations…</StorageNotice>}
      {(locations.isError || destinations.isError) && <StorageNotice error>Migration locations could not be loaded. <Button onClick={() => {locations.refetch(); destinations.refetch();}}>Retry locations</Button></StorageNotice>}
      <form onSubmit={submit}>
        <fieldset className="storage-fieldset" disabled={preview.isPending || start.isPending}>
          <label className="tl-field"><span className="microlabel">Source location on this device</span><select aria-label="Source location on this device" required className="tl-select" value={source} onChange={e => {setSource(e.target.value); resetPreview();}}><option value="">Choose a source location</option>{locations.data?.items.map(l => <option key={l.location_id} value={l.location_id} disabled={l.state !== "ready"}>{l.label || l.path}{l.state !== "ready" ? ` · ${l.state}` : ""}</option>)}</select></label>
          <DestinationPicker label="Migration destination" value={destination} destinations={destinations.data?.items ?? []} onChange={v => {setDestination(v); resetPreview();}} />
          <details className="storage-section"><summary>Limit content types (all by default)</summary><div className="storage-kind-checks">{CONTENT_KINDS.map(k => <label className="storage-check" key={k}><input type="checkbox" checked={kinds.includes(k)} onChange={e => {setKinds(current => e.target.checked ? [...current, k] : current.filter(x => x !== k)); resetPreview();}} /> {kindLabel(k)}</label>)}</div></details>
          <label className="tl-field"><span className="microlabel">Operation</span><select aria-label="Operation" className="tl-select" value={removeSource ? "move" : "copy"} onChange={e => {setRemoveSource(e.target.value === "move"); resetPreview();}}><option value="copy">Copy · retain originals</option><option value="move">Move · remove source after verification</option></select></label>
          <Button type="submit" variant="outline">{preview.isPending ? "Preparing preview…" : "Preview existing content"}</Button>
        </fieldset>
      </form>
      {preview.isError && <StorageNotice error>{storageError(preview.error)}</StorageNotice>}
      {preview.data && <section className="storage-section" aria-label="Migration preview">
        <h2 className="storage-heading">Review {preview.data.remove_source ? "move" : "copy"}</h2>
        <p>{preview.data.item_count} {preview.data.item_count === 1 ? "item" : "items"} · {fmtBytes(preview.data.total_bytes)} · expires {fmtDateTime(preview.data.expires_at)}</p>
        <dl className="storage-stats"><div><dt>From</dt><dd><strong>{sourceLocation?.label || sourceLocation?.path || "Source location not reported"}</strong><br />{sourceNode?.node_name || "Source device name not reported"}</dd></div><div><dt>To</dt><dd><strong>{destinationLocation?.label || destinationLocation?.path || "Destination location not reported"}</strong><br />{destinationNode?.node_name || "Destination device name not reported"}</dd></div></dl>
        <details><summary>Reviewed storage identities</summary><p className="storage-id mono">Source location: {preview.data.source_location_id}<br />Destination node: {preview.data.destination.node_id}<br />Destination location: {preview.data.destination.location_id || "not reported"}</p></details>
        {preview.data.blockers.map((b, i) => <StorageNotice key={i} error>{b}</StorageNotice>)}
        {preview.data.items.length === 0 && <p className="storage-empty">No eligible content in this preview.</p>}
        <ul className="storage-preview-items">{preview.data.items.map((item, index) => <li key={`${item.artifact_id}:${index}`}><strong>{item.name || kindLabel(item.kind)}</strong><span>{kindLabel(item.kind)} · {fmtBytes(item.size_bytes)} · {item.action}</span>{item.reason && <span>{item.reason}</span>}</li>)}</ul>
        {preview.data.remove_source && <label className="storage-check"><input type="checkbox" checked={acknowledged} disabled={start.isPending || start.isSuccess} onChange={e => setAcknowledged(e.target.checked)} /> I reviewed these items and approve removing their source copies after verification.</label>}
        {expired && !start.isSuccess && <StorageNotice error>This preview expired. Prepare a new preview before starting.</StorageNotice>}
        {start.isError && <StorageNotice error>{storageError(start.error)} Prepare a fresh preview if the source or destination changed.</StorageNotice>}
        {start.isSuccess ? <StorageNotice>{start.data.remove_source ? "Move" : "Copy"} started. Progress is shown below.</StorageNotice> : <Button variant={preview.data.remove_source ? "danger" : "primary"} disabled={start.isPending || !preview.data.can_start || preview.data.item_count === 0 || expired || (preview.data.remove_source && !acknowledged)} onClick={() => start.mutate(preview.data!.preview_id)}>{start.isPending ? "Starting…" : `Start reviewed ${preview.data.remove_source ? "move" : "copy"}`}</Button>}
      </section>}
    </section>
    <section className="panel" aria-label="Migration progress">
      <div className="panel-title">Migration progress</div>
      {jobs.isPending && <StorageNotice>Loading migrations…</StorageNotice>}
      {jobs.isError && <StorageNotice error>{jobs.data ? "Progress may be stale. " : ""}{storageError(jobs.error)} <Button onClick={() => jobs.refetch()}>Retry migrations</Button></StorageNotice>}
      {jobs.data?.items.length === 0 && <p className="storage-empty">No migrations started on this device.</p>}
      {control.isError && <StorageNotice error>{storageError(control.error)}</StorageNotice>}
      <div className="storage-list">{jobs.data?.items.map(job => <article className="storage-item" key={job.migration_id}>
        <div className="storage-row-head"><h3 className="storage-heading">{job.remove_source ? "Move" : "Copy"} · {job.state}</h3><span className="badge">{job.phase}</span></div>
        <progress className="storage-progress" value={job.completed_items} max={Math.max(job.total_items, 1)} aria-label={`Migration ${job.migration_id} progress`} />
        <p className="storage-meta">{job.completed_items} / {job.total_items} items · {fmtBytes(job.bytes_done)} / {fmtBytes(job.total_bytes)}</p><p>{job.detail}</p>
        <div className="storage-actions">{job.can_cancel && <Button variant="outline" disabled={control.isPending} onClick={() => control.mutate({id: job.migration_id, action: "cancel"})}>Cancel migration</Button>}{job.can_resume && <Button variant="outline" disabled={control.isPending} onClick={() => control.mutate({id: job.migration_id, action: "resume"})}>Resume migration</Button>}</div>
      </article>)}</div>
    </section>
  </div>;
}
