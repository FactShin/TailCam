import { WorkloadJobsView } from "../components/WorkloadJobsView";
import { WorkloadPlacement } from "../components/WorkloadPlacement";
import { TrainingSupervisor } from "../components/TrainingSupervisor";
import { useSearchParams } from "react-router-dom";
import { Button } from "../components/ui";

const TABS = [{ id: "placement", label: "Placement" }, { id: "jobs", label: "Jobs" }, { id: "supervisor", label: "Training Supervisor" }];

export function Workloads() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some(item => item.id === params.get("tab")) ? params.get("tab")! : "placement";
  return <div className="screen workload-screen">
    <div className="screen-head"><div><div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Workload console</span></div><h1 className="screen-title">Workloads</h1><p className="screen-sub">Choose where work runs and follow its progress.</p></div></div>
    <nav className="storage-tabs" aria-label="Workload views">{TABS.map(item => <Button key={item.id} variant={tab === item.id ? "primary" : "outline"} aria-current={tab === item.id ? "page" : undefined} onClick={() => setParams({ tab: item.id })}>{item.label}</Button>)}</nav>
    {tab === "placement" && <WorkloadPlacement />}{tab === "jobs" && <WorkloadJobsView />}{tab === "supervisor" && <TrainingSupervisor />}
  </div>;
}
