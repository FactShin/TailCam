import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { useHosts } from "../api/hooks";
import { useSaveStoragePolicy, useStorageDestinations, useStoragePolicy } from "../api/storageHooks";
import { IconHdd } from "../icons";
import { CONTENT_KINDS, type ContentKind, type DestinationRef, type StorageDestination, type StorageOverride, type StoragePolicyStatus, type UnifiedStoragePolicy } from "../storageTypes";
import { Button } from "./ui";

export const GIB = 1024 ** 3;
export const kindLabel = (value: string) => ({recording: "Recordings", snapshot: "Snapshots", thumbnail: "Thumbnails", timelapse_frame: "Timelapse frames", timelapse_video: "Timelapse videos", timelapse_smooth: "Smoothed timelapses", analysis_evidence: "Analysis evidence", training_sample: "Training samples", annotation: "Annotations", model_output: "Model outputs", export: "Exports"}[value] ?? value.split("_").join(" "));
export const storageError = (error: unknown) => error instanceof ApiError && error.status === 403
  ? "An administrator connection is required to change storage."
  : error instanceof ApiError && error.status === 404
    ? "This device does not report unified storage. Its existing storage settings remain available."
    : error instanceof Error ? error.message : "Storage could not be reached. Try again.";
export function StorageNotice({children, error = false}: {children: ReactNode; error?: boolean}) {
  return <div className={`storage-notice ${error ? "is-error" : ""}`} role={error ? "alert" : "status"}>{children}</div>;
}
export function NumberField({label, value, onChange, min = 0, max, step = 1}: {label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number | "any"}) {
  return <label className="tl-field"><span className="microlabel">{label}</span><input required className="tl-input" type="number" min={min} max={max} step={step} value={Number.isFinite(value) ? value : ""} onChange={e => onChange(e.target.valueAsNumber)} /></label>;
}
export function DestinationPicker({label, value, onChange, destinations}: {label: string; value: DestinationRef; onChange: (v: DestinationRef) => void; destinations: StorageDestination[]}) {
  const node = destinations.find(n => n.node_id === value.node_id);
  const locations = node?.locations ?? [];
  return <div className="storage-destination">
    <label className="tl-field"><span className="microlabel">{label} device</span>
      <select aria-label={`${label} device`} required className="tl-select" value={value.node_id} onChange={e => onChange({node_id: e.target.value, location_id: null})}>
        <option value="" disabled>Choose a device</option>
        {value.node_id && !node && <option value={value.node_id}>{value.node_id} · not reported</option>}
        {destinations.map(n => <option key={n.node_id || n.node_key} value={n.node_id} disabled={!n.node_id || !n.supported || !n.online}>{n.node_name || n.node_key}{!n.supported ? " · storage protocol not reported" : !n.online ? " · offline" : ""}</option>)}
      </select>
    </label>
    <label className="tl-field"><span className="microlabel">{label} location</span>
      <select aria-label={`${label} location`} className="tl-select" value={value.location_id ?? ""} onChange={e => onChange({...value, location_id: e.target.value || null})}>
        <option value="">Device default at admission</option>
        {value.location_id && !locations.some(l => l.location_id === value.location_id) && <option value={value.location_id}>{value.location_id} · not reported</option>}
        {locations.map(l => <option key={l.location_id} value={l.location_id} disabled={l.state !== "ready"}>{l.label || l.path}{l.state !== "ready" ? ` · ${l.state}` : ""}</option>)}
      </select>
    </label>
  </div>;
}
export function StoragePolicyPanel() {
  const q = useStoragePolicy();
  return <section className="panel" aria-label="Unified storage summary">
    <div className="panel-title"><IconHdd size={16} /> Unified storage</div>
    <p className="ais-intro">Choose destinations for every content type, check capacity, and review transfers or moves.</p>
    {q.isPending ? <StorageNotice>Loading storage policy…</StorageNotice> : q.isError ? <StorageNotice error>{storageError(q.error)}</StorageNotice>
      : <StorageNotice>{q.data.enabled ? `Unified policy active · revision ${q.data.policy.revision}` : "Legacy placement is active. Review and apply a policy to include every content type."}</StorageNotice>}
    <Link className="btn btn-outline" to="/storage">Open storage</Link>
  </section>;
}

export function StoragePolicyEditor({status}: {status: StoragePolicyStatus}) {
  // A saved snapshot is separate from the draft: polling must never replace edits.
  const [draft, setDraft] = useState<UnifiedStoragePolicy>(() => structuredClone(status.policy));
  const [baseRevision, setBaseRevision] = useState(status.policy.revision);
  const [success, setSuccess] = useState(false);
  const destinations = useStorageDestinations();
  const current = useStoragePolicy();
  const hosts = useHosts().data ?? [];
  const save = useSaveStoragePolicy();
  const set = (patch: Partial<UnifiedStoragePolicy>) => {setDraft(d => ({...d, ...patch})); setSuccess(false); save.reset();};
  const reset = async () => {const latest = await current.refetch(); if (latest.isError || !latest.data) return; setDraft(structuredClone(latest.data.policy)); setBaseRevision(latest.data.policy.revision); save.reset(); setSuccess(false);};
  const dirty = JSON.stringify(draft) !== JSON.stringify(status.policy);
  const selectors = draft.overrides.map(o => JSON.stringify([o.origin_node_id, o.camera_id, o.content_kind]));
  const duplicateOverrides = new Set(selectors).size !== selectors.length;
  const replaceOverride = (index: number, value: StorageOverride) => set({overrides: draft.overrides.map((x, i) => i === index ? value : x)});
  const knownOrigins = hosts.filter(h => h.node_id);
  const addOverride = () => set({overrides: [...draft.overrides, {origin_node_id: null, camera_id: null, content_kind: "snapshot", destination: {...draft.default_destination}}]});
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const result = await save.mutateAsync({expected_revision: baseRevision, policy: draft});
      setDraft(structuredClone(result.policy)); setBaseRevision(result.policy.revision); setSuccess(true);
    } catch { /* Keep the complete draft and show the mutation error. */ }
  };
  return <form className="panel storage-policy-form" onSubmit={submit}>
    <div className="panel-title">Storage policy for this device</div>
    <p className="ais-intro">New content uses this policy. Existing content stays at its recorded location until you review a separate move.</p>
    <p className="storage-id mono">Source node: {status.source_node_id}</p>
    {!status.enabled && <StorageNotice>Legacy placement is still active. Applying this policy enables unified placement for new content on this device.</StorageNotice>}
    {status.policy.revision !== baseRevision && <StorageNotice error>A newer policy is available. Your draft is preserved. Reload the saved policy before applying another revision.</StorageNotice>}
    {destinations.isError && <StorageNotice error>{storageError(destinations.error)} <Button type="button" onClick={() => destinations.refetch()}>Retry destinations</Button></StorageNotice>}
    {destinations.isPending && <StorageNotice>Loading storage destinations…</StorageNotice>}
    <fieldset disabled={save.isPending} className="storage-fieldset">
      <DestinationPicker label="Default" value={draft.default_destination} onChange={default_destination => set({default_destination})} destinations={destinations.data?.items ?? []} />
      <p className="stor-note">A device default is resolved once when a job is admitted. The job keeps that location even if the default later changes.</p>
      <label className="tl-field"><span className="microlabel">If the destination is unavailable</span>
        <select aria-label="If the destination is unavailable" className="tl-select" value={draft.outage_policy} onChange={e => {
          const outage_policy = e.target.value as UnifiedStoragePolicy["outage_policy"];
          set({outage_policy, secondary_destination: outage_policy === "secondary" ? draft.secondary_destination ?? {...draft.default_destination} : null});
        }}>
          <option value="destination_required">Required destination</option>
          <option value="local_spool" disabled={draft.zero_local_media}>Bounded local spool</option>
          <option value="secondary">Secondary destination</option>
        </select>
      </label>
      <p className="stor-note">{draft.outage_policy === "destination_required" ? "New content is rejected while its destination is unavailable." : draft.outage_policy === "local_spool" ? "Pending content stays in a bounded local queue until its primary destination verifies a copy." : "Content goes to the chosen secondary while the primary is unavailable. The catalog shows its actual owner."}</p>
      {draft.outage_policy === "secondary" && <DestinationPicker label="Secondary" value={draft.secondary_destination ?? draft.default_destination} onChange={secondary_destination => set({secondary_destination})} destinations={destinations.data?.items ?? []} />}
      {draft.outage_policy === "local_spool" && <div className="storage-fields">
        <NumberField label="Local queue limit (GiB)" value={draft.spool_max_bytes / GIB} min={0.001} step="any" onChange={v => set({spool_max_bytes: Math.round(v * GIB)})} />
        <NumberField label="Local queue maximum age (hours)" value={draft.spool_max_age_seconds / 3600} min={1 / 3600} step="any" onChange={v => set({spool_max_age_seconds: Math.round(v * 3600)})} />
      </div>}
      <label className="storage-check"><input type="checkbox" checked={draft.zero_local_media} onChange={e => set({zero_local_media: e.target.checked, ...(e.target.checked && draft.outage_policy === "local_spool" ? {outage_policy: "destination_required" as const} : {})})} /> Keep no local media on this source</label>
      <p className="stor-note">With no local media, unavailable destinations prevent capture. Temporary processing is separately bounded below.</p>
      <div className="storage-fields">
        <NumberField label="Temporary workspace limit (GiB)" value={draft.workspace_max_bytes / GIB} step="any" onChange={v => set({workspace_max_bytes: Math.round(v * GIB)})} />
        <NumberField label="Maximum single artifact (GiB)" value={draft.artifact_max_bytes / GIB} min={0.001} step="any" onChange={v => set({artifact_max_bytes: Math.round(v * GIB)})} />
      </div>
      <label className="tl-field"><span className="microlabel">After the primary destination verifies a copy</span>
        <select aria-label="After the primary destination verifies a copy" className="tl-select" value={draft.source_cleanup} onChange={e => set({source_cleanup: e.target.value as UnifiedStoragePolicy["source_cleanup"]})}>
          <option value="after_primary_commit">Remove the temporary source copy</option><option value="retain">Retain the source copy</option>
        </select>
      </label>
      <section className="storage-section" aria-label="Storage overrides">
        <h2 className="storage-heading">Camera and content overrides</h2>
        <p className="stor-note">Camera + content wins first, then camera, then content type, then the default. Removing a rule restores inheritance.</p>
        {draft.overrides.length === 0 && <p className="storage-empty">No overrides. Every content type inherits the default.</p>}
        {draft.overrides.map((o, i) => <div className="storage-rule" key={i}>
          <div className="storage-row-head"><h3 className="storage-heading">Override {i + 1}</h3><Button type="button" size="sm" onClick={() => set({overrides: draft.overrides.filter((_, n) => n !== i)})}>Remove override {i + 1}</Button></div>
          <div className="storage-fields">
            <label className="tl-field"><span className="microlabel">Override {i + 1} origin</span><select aria-label={`Override ${i + 1} origin`} className="tl-select" value={o.origin_node_id ?? ""} onChange={e => replaceOverride(i, {...o, origin_node_id: e.target.value || null, camera_id: e.target.value ? o.camera_id : null})}>
              <option value="">Any origin</option>{o.origin_node_id && !knownOrigins.some(h => h.node_id === o.origin_node_id) && <option value={o.origin_node_id}>{o.origin_node_id}</option>}{knownOrigins.map(h => <option key={h.node_key} value={h.node_id!}>{h.node_name || h.host}</option>)}
            </select></label>
            <label className="tl-field"><span className="microlabel">Override {i + 1} camera ID (blank = any)</span><input className="tl-input" maxLength={256} disabled={!o.origin_node_id} value={o.camera_id ?? ""} placeholder="/dev/video0" onChange={e => replaceOverride(i, {...o, camera_id: e.target.value || null})} /></label>
            <label className="tl-field"><span className="microlabel">Override {i + 1} content</span><select aria-label={`Override ${i + 1} content`} className="tl-select" required={!o.camera_id} value={o.content_kind ?? ""} onChange={e => replaceOverride(i, {...o, content_kind: (e.target.value || null) as ContentKind | null})}><option value="" disabled={!o.camera_id}>Any content</option>{CONTENT_KINDS.map(k => <option key={k} value={k}>{kindLabel(k)}</option>)}</select></label>
          </div>
          <DestinationPicker label={`Override ${i + 1}`} value={o.destination} onChange={destination => replaceOverride(i, {...o, destination})} destinations={destinations.data?.items ?? []} />
        </div>)}
        <Button type="button" variant="outline" onClick={addOverride} disabled={draft.overrides.length >= 256}>Add override</Button>
        {duplicateOverrides && <StorageNotice error>Two overrides select the same origin, camera, and content. Change a selector or remove the duplicate rule.</StorageNotice>}
      </section>
      <details className="storage-section"><summary>Retention for new artifacts</summary>
        <p className="stor-note">Retention controls deletion of stored artifacts. It is separate from free-space admission and temporary source cleanup.</p>
        <label className="storage-check"><input type="checkbox" checked={draft.retention.enabled} onChange={e => set({retention: {...draft.retention, enabled: e.target.checked}})} /> Enable age-based retention</label>
        {draft.retention.enabled && <NumberField label="Retention age (days)" min={1} value={draft.retention.max_age_seconds / 86400} onChange={v => set({retention: {...draft.retention, max_age_seconds: v * 86400}})} />}
        <NumberField label="Minimum verified replicas" min={1} max={10} value={draft.retention.min_replicas} onChange={v => set({retention: {...draft.retention, min_replicas: v}})} />
        <label className="storage-check"><input type="checkbox" checked={draft.retention.protect} onChange={e => set({retention: {...draft.retention, protect: e.target.checked}})} /> Protect artifacts from automatic deletion</label>
      </details>
      {save.isError && <StorageNotice error>{save.error instanceof ApiError && save.error.status === 409 ? "The policy changed or cannot be applied. Your draft is preserved. Review the saved revision and destination availability." : storageError(save.error)}</StorageNotice>}
      {success && <StorageNotice>Policy applied. New content uses revision {baseRevision}. Existing files were not moved.</StorageNotice>}
      <div className="storage-actions"><Button type="submit" variant="primary" disabled={save.isPending || duplicateOverrides || (status.enabled && !dirty) || baseRevision !== status.policy.revision}>{save.isPending ? "Applying…" : "Apply storage policy"}</Button><Button type="button" variant="outline" disabled={current.isFetching} onClick={reset}>Reload saved policy</Button></div>
    </fieldset>
  </form>;
}
