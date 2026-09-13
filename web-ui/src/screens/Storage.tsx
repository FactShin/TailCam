import { useSearchParams } from "react-router-dom";
import { useStoragePolicy } from "../api/storageHooks";
import { StorageContent, StorageTransfers } from "../components/StorageContent";
import { StorageAdmissionCheck, StorageLocations } from "../components/StorageLocations";
import { StorageMigrations } from "../components/StorageMigrations";
import { storageError, StorageNotice, StoragePolicyEditor } from "../components/StoragePolicyPanel";
import { Button } from "../components/ui";

const TABS = [{id: "policy", label: "Policy & locations"}, {id: "content", label: "Content"}, {id: "transfers", label: "Transfers"}, {id: "migrations", label: "Move existing content"}];
export function Storage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some(t => t.id === params.get("tab")) ? params.get("tab")! : "policy";
  const policy = useStoragePolicy();
  return <div className="screen storage-screen">
    <div className="screen-head"><div><div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Storage console</span></div><h1 className="screen-title">Storage</h1><p className="screen-sub">Destinations, capacity, and verified content</p></div></div>
    <nav className="storage-tabs" aria-label="Storage views">{TABS.map(t => <button key={t.id} className={`btn ${tab === t.id ? "btn-primary" : "btn-outline"}`} aria-current={tab === t.id ? "page" : undefined} onClick={() => setParams({tab: t.id})}>{t.label}</button>)}</nav>
    {tab === "policy" && <div className="storage-stack">
      {policy.isPending && <StorageNotice>Loading storage policy…</StorageNotice>}
      {policy.isError && <StorageNotice error>{storageError(policy.error)} <Button onClick={() => policy.refetch()}>Retry policy</Button></StorageNotice>}
      {policy.data && <><StoragePolicyEditor status={policy.data} /><StorageLocations /><StorageAdmissionCheck sourceNodeId={policy.data.source_node_id} enabled={policy.data.enabled} /></>}
      {policy.isError && !policy.data && <a className="btn btn-outline" href="/settings">Open existing storage settings</a>}
    </div>}
    {tab === "content" && <StorageContent />}{tab === "transfers" && <StorageTransfers />}{tab === "migrations" && <StorageMigrations />}
  </div>;
}
