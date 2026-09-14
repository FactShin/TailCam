import { useState } from "react";
import { useRegisterStorageLocation, useStorageAdmission, useStorageLocations, useUpdateStorageLocation } from "../api/storageHooks";
import { fmtBytes } from "../lib/format";
import { CONTENT_KINDS, type ContentKind, type StorageLocation } from "../storageTypes";
import { FolderPicker } from "./FolderPicker";
import { GIB, kindLabel, NumberField, storageError, StorageNotice } from "./StoragePolicyPanel";
import { Button } from "./ui";

function LocationLimits({location}: {location: StorageLocation}) {
  const update = useUpdateStorageLocation();
  const [open, setOpen] = useState(false);
  const [quota, setQuota] = useState(location.quota_bytes / GIB);
  const [reserve, setReserve] = useState(location.reserve_bytes / GIB);
  const [makeDefault, setMakeDefault] = useState(false);
  if (!open) return <Button size="sm" variant="outline" onClick={() => {setQuota(location.quota_bytes / GIB); setReserve(location.reserve_bytes / GIB); setMakeDefault(false); update.reset(); setOpen(true);}}>Edit limits for {location.label || "this location"}</Button>;
  return <form className="storage-section" aria-label={`Limits for ${location.label || location.location_id}`} onSubmit={e => {e.preventDefault(); update.mutate({id: location.location_id, quota_bytes: Math.round(quota * GIB), reserve_bytes: Math.round(reserve * GIB), ...(makeDefault ? {make_default: true} : {})});}}>
    <fieldset className="storage-fieldset" disabled={update.isPending}>
      <div className="storage-fields"><NumberField label="Quota (GiB, 0 = no quota)" value={quota} step="any" onChange={setQuota} /><NumberField label="Keep free (GiB)" value={reserve} step="any" onChange={setReserve} /></div>
      {!location.is_default && <label className="storage-check"><input type="checkbox" checked={makeDefault} onChange={e => setMakeDefault(e.target.checked)} /> Make this the default for future admissions</label>}
      <p className="stor-note">Limits affect new admissions. Reducing a quota does not delete files.</p>
      {update.isError && <StorageNotice error>{storageError(update.error)}</StorageNotice>}{update.isSuccess && <StorageNotice>Location limits saved.</StorageNotice>}
      <div className="storage-actions"><Button type="submit" variant="primary">{update.isPending ? "Saving…" : "Save location limits"}</Button><Button type="button" onClick={() => setOpen(false)}>Close limits</Button></div>
    </fieldset>
  </form>;
}

export function StorageLocations() {
  const q = useStorageLocations();
  const register = useRegisterStorageLocation();
  const [path, setPath] = useState("");
  const [label, setLabel] = useState("");
  const [quota, setQuota] = useState(0);
  const [reserve, setReserve] = useState(1);
  const [makeDefault, setMakeDefault] = useState(false);
  const [browse, setBrowse] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try { await register.mutateAsync({path, label, quota_bytes: Math.round(quota * GIB), reserve_bytes: Math.round(reserve * GIB), make_default: makeDefault}); setPath(""); setLabel(""); setMakeDefault(false); } catch { /* draft retained */ }
  };
  return <section className="panel" aria-label="Storage locations">
    <div className="panel-title">Locations on this device</div>
    <p className="ais-intro">Register a folder on a mounted drive. A changed or missing drive stops admission; TailCam keeps the original location identity for existing content.</p>
    {q.isPending && <StorageNotice>Loading locations…</StorageNotice>}
    {q.isError && <StorageNotice error>{storageError(q.error)} <Button onClick={() => q.refetch()}>Retry locations</Button></StorageNotice>}
    {q.data?.items.length === 0 && <p className="storage-empty">No locations registered on this device.</p>}
    <div className="storage-list">{q.data?.items.map(l => <article className="storage-item" key={l.location_id}>
      <div className="storage-row-head"><h3 className="storage-heading">{l.label || "Storage location"}</h3><span className={`badge ${l.state === "ready" ? "badge-ok" : "badge-warn"}`}>{l.state}{l.is_default ? " · default" : ""}</span></div>
      <p className="storage-path mono">{l.path}</p><p className="storage-id mono">Location: {l.location_id}</p>
      <dl className="storage-stats"><div><dt>Available for admission</dt><dd>{l.allocatable_bytes === null ? "Not reported" : fmtBytes(l.allocatable_bytes)}</dd></div><div><dt>Disk free</dt><dd>{l.free_bytes === null ? "Not reported" : fmtBytes(l.free_bytes)}</dd></div><div><dt>Used / reserved by jobs</dt><dd>{fmtBytes(l.used_bytes)} / {fmtBytes(l.reserved_bytes)}</dd></div><div><dt>Quota / keep free</dt><dd>{l.quota_bytes ? fmtBytes(l.quota_bytes) : "No quota"} / {fmtBytes(l.reserve_bytes)}</dd></div></dl>
      <LocationLimits location={l} />
    </article>)}</div>
    <details className="storage-section"><summary>Register another location</summary>
      <form onSubmit={submit}>
        <fieldset disabled={register.isPending} className="storage-fieldset">
          <div className="storage-fields"><label className="tl-field"><span className="microlabel">Location label</span><input className="tl-input" maxLength={64} value={label} onChange={e => setLabel(e.target.value)} placeholder="Archive drive" /></label><label className="tl-field"><span className="microlabel">Local folder path</span><input className="tl-input" required value={path} onChange={e => setPath(e.target.value)} placeholder="/mnt/archive/tailcam" /></label></div>
          <Button type="button" variant="outline" onClick={() => setBrowse(true)}>Browse local folders</Button>
          <div className="storage-fields"><NumberField label="Location quota (GiB, 0 = no quota)" value={quota} step="any" onChange={setQuota} /><NumberField label="Reserved free space (GiB)" value={reserve} step="any" onChange={setReserve} /></div>
          <label className="storage-check"><input type="checkbox" checked={makeDefault} onChange={e => setMakeDefault(e.target.checked)} /> Use as this device's default for future admissions</label>
          <p className="stor-note">This creates a location marker. Existing media is not moved or deleted. Manage other devices' folders from their own storage page.</p>
          {register.isError && <StorageNotice error>{storageError(register.error)}</StorageNotice>}
          {register.isSuccess && <StorageNotice>Location registered. Existing content remains at its recorded location.</StorageNotice>}
          <Button type="submit" variant="primary">{register.isPending ? "Registering…" : "Register location"}</Button>
        </fieldset>
      </form>
    </details>
    {browse && <FolderPicker prefix="" host="this device" initialPath={path} onPick={picked => {setPath(picked); setBrowse(false);}} onClose={() => setBrowse(false)} />}
  </section>;
}

export function StorageAdmissionCheck({sourceNodeId, enabled}: {sourceNodeId: string; enabled: boolean}) {
  const check = useStorageAdmission();
  const [kind, setKind] = useState<ContentKind>("recording");
  const [camera, setCamera] = useState("");
  const [size, setSize] = useState(100);
  const [workspace, setWorkspace] = useState(false);
  const changed = () => check.reset();
  return <form className="panel" aria-label="Storage admission check" onSubmit={e => {e.preventDefault(); check.mutate({origin_node_id: sourceNodeId, camera_id: camera, kind, size_bytes: Math.round(size * 1024 ** 2), requires_workspace: workspace});}}>
    <div className="panel-title">Check a new capture</div>
    <p className="ais-intro">Preview the saved policy and current capacity without creating content. This check is not a reservation; conditions can change before capture starts.</p>
    {!enabled && <StorageNotice>Unified placement is not active yet. This previews the saved unified policy; apply it before relying on it for new captures.</StorageNotice>}
    <fieldset className="storage-fieldset" disabled={check.isPending}>
      <div className="storage-fields"><label className="tl-field"><span className="microlabel">Check content type</span><select aria-label="Check content type" className="tl-select" value={kind} onChange={e => {setKind(e.target.value as ContentKind); changed();}}>{CONTENT_KINDS.map(k => <option key={k} value={k}>{kindLabel(k)}</option>)}</select></label><label className="tl-field"><span className="microlabel">Check camera ID (optional)</span><input className="tl-input" maxLength={256} value={camera} onChange={e => {setCamera(e.target.value); changed();}} /></label><NumberField label="Estimated content size (MiB)" min={0.001} step="any" value={size} onChange={v => {setSize(v); changed();}} /></div>
      <Button type="submit" variant="outline">{check.isPending ? "Checking…" : "Check admission"}</Button>
      <label className="storage-check"><input type="checkbox" checked={workspace} onChange={e => {setWorkspace(e.target.checked); changed();}} /> This job needs temporary processing space</label>
    </fieldset>
    {check.isError && <StorageNotice error>{storageError(check.error)}</StorageNotice>}
    {check.data && <StorageNotice error={!check.data.allowed}>
      <strong>{check.data.allowed ? "Can accept this content" : "Cannot accept this content"}</strong>
      <p>{check.data.detail || (check.data.spooled ? "The primary is unavailable. Content would enter the bounded local queue." : "The saved policy has an available destination.")}</p>
      {check.data.code && <p className="mono">{check.data.code}</p>}
      {check.data.destination && <p className="storage-id mono">Destination: {check.data.destination.node_id}<br />Location: {check.data.destination.location_id || "device default"}</p>}
    </StorageNotice>}
  </form>;
}
