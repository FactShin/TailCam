import { useEffect, useState } from "react";
import { useNodeCapabilities } from "../api/hooks";
import { usePlacementPolicy, useSavePlacementPolicy, useSaveWorkloadProvider, useWorkloadProviders, useWorkloadWorkers } from "../api/workloadHooks";
import { fmtBytes } from "../lib/format";
import { TASK_LABELS, WORKLOAD_TASKS, type PlacementPolicy, type PlacementPolicyStatus, type TaskRoute, type WorkerTarget, type WorkloadProvider, type WorkloadTask, type WorkloadWorker } from "../workloadTypes";
import { Button, Toggle } from "./ui";
import { defaultWorkloadBudget, WorkloadBudgetEditor, workloadBudgetError } from "./WorkloadBudgetEditor";
import { workloadError, WorkloadNotice } from "./WorkloadCommon";

const RESERVED: WorkloadTask[] = ["speech_recognition", "conversation", "speech_synthesis"];
const emptyRoute = (node: string): TaskRoute => ({ mode: "manual", target: { node_id: node, provider_id: null, model: "" }, approved_node_ids: [], fallback_targets: [], budget: defaultWorkloadBudget() });
const targetKey = (target: WorkerTarget | null) => target?.provider_id ? `provider:${target.provider_id}` : target?.node_id ? `node:${target.node_id}` : "";

export function WorkerSelect({ value, onChange, workers, providers, task, label, disabled = false }: {
  value: WorkerTarget | null; onChange: (target: WorkerTarget | null) => void; workers: WorkloadWorker[];
  providers: WorkloadProvider[]; task: WorkloadTask; label: string; disabled?: boolean;
}) {
  const available = [...workers.map(worker => `node:${worker.node_id}`), ...providers.map(provider => `provider:${provider.provider_id}`)];
  return <label className="tl-field"><span className="microlabel">{label}</span><select className="tl-select" aria-label={label} value={targetKey(value)} disabled={disabled} onChange={event => {
    const key = event.target.value;
    if (!key) return onChange(null);
    if (key.startsWith("provider:")) {
      const provider = providers.find(item => item.provider_id === key.slice(9));
      return onChange({ node_id: null, provider_id: key.slice(9), model: provider?.model ?? "" });
    }
    onChange({ node_id: key.slice(5), provider_id: null, model: value?.model ?? "" });
  }}>
    <option value="">Choose a worker or endpoint</option>
    {workers.map(worker => {
      const status = worker.tasks.find(item => item.task === task);
      return <option key={worker.node_id} value={`node:${worker.node_id}`} disabled={!worker.online || !status || ["unavailable", "disabled"].includes(status.state)}>{worker.name || worker.node_id} · {worker.online ? status?.state ?? "not reported" : "offline"}</option>;
    })}
    {providers.map(provider => <option key={provider.provider_id} value={`provider:${provider.provider_id}`} disabled={!provider.enabled || !provider.tasks.includes(task)}>{provider.name} · model endpoint{provider.enabled ? "" : " · disabled"}</option>)}
    {value && !available.includes(targetKey(value)) && <option value={targetKey(value)} disabled>{value.provider_id || value.node_id} · not reported</option>}
  </select></label>;
}

export function WorkloadPlacement() {
  const policy = usePlacementPolicy();
  const workers = useWorkloadWorkers();
  const providers = useWorkloadProviders();
  const principal = useNodeCapabilities("local").data?.principal;
  const admin = !!principal?.verified && principal.roles.includes("admin");
  return <div className="workload-stack">
    {policy.isPending && <WorkloadNotice>Loading workload placement…</WorkloadNotice>}
    {policy.isError && <WorkloadNotice error>{policy.data ? "Saved placement may be stale. " : ""}{workloadError(policy.error)} <Button onClick={() => policy.refetch()}>Retry placement</Button></WorkloadNotice>}
    {(workers.isPending || providers.isPending) && <WorkloadNotice>Loading approved workers and model endpoints…</WorkloadNotice>}
    {workers.isError && <WorkloadNotice error>{workloadError(workers.error)} <Button onClick={() => workers.refetch()}>Retry workers</Button></WorkloadNotice>}
    {providers.isError && <WorkloadNotice error>{workloadError(providers.error)} <Button onClick={() => providers.refetch()}>Retry endpoints</Button></WorkloadNotice>}
    {policy.data && <PlacementEditor status={policy.data} workers={workers.data?.items ?? []} providers={providers.data?.items ?? []} admin={admin} />}
    {providers.data && <ProviderEditor providers={providers.data.items} admin={admin} />}
  </div>;
}

function PlacementEditor({ status, workers, providers, admin }: { status: PlacementPolicyStatus; workers: WorkloadWorker[]; providers: WorkloadProvider[]; admin: boolean }) {
  const [draft, setDraft] = useState<PlacementPolicy>(() => structuredClone(status.policy));
  const [dirty, setDirty] = useState(false);
  const [task, setTask] = useState<WorkloadTask>("live_detection");
  const [fallback, setFallback] = useState<WorkerTarget | null>(null);
  const [notice, setNotice] = useState("");
  const save = useSavePlacementPolicy();
  const latest = usePlacementPolicy();
  useEffect(() => { if (!dirty) setDraft(structuredClone(status.policy)); }, [status.policy, dirty]);
  const configured = !!draft.routes[task];
  const rule = draft.routes[task] ?? emptyRoute(status.source_node_id);
  const update = (next: TaskRoute) => { setDraft(current => ({ ...current, routes: { ...current.routes, [task]: next } })); setDirty(true); setNotice(""); };
  const problem = Object.entries(draft.routes).reduce<string | null>((error, [name, route]) => error ?? (route ? workloadBudgetError(route.budget) || (route.mode === "manual" && !route.target ? `${TASK_LABELS[name as WorkloadTask]} needs a selected worker.` : route.mode === "auto" && !route.approved_node_ids.length ? `${TASK_LABELS[name as WorkloadTask]} needs at least one approved Auto worker.` : null) : null), null);
  const selected = workers.find(worker => worker.node_id === rule.target?.node_id);
  const taskStatus = selected?.tasks.find(item => item.task === task);
  return <div className="panel">
    <div className="workload-heading"><h2 className="panel-title">Placement policy</h2><span className="badge">Revision {draft.revision}</span></div>
    <p className="workload-help">Save explicit task routes for new work. Existing jobs retain their approved route. Auto uses only your approved worker list; fallbacks are limited to the targets you add.</p>
    {!admin && <WorkloadNotice>Administrator permissions are required to change placement.</WorkloadNotice>}
    <div className="workload-form-grid"><label className="tl-field"><span className="microlabel">Task</span><select className="tl-select" aria-label="Placement task" value={task} onChange={event => { setTask(event.target.value as WorkloadTask); setFallback(null); }}>
      {WORKLOAD_TASKS.map(value => <option value={value} key={value} disabled={RESERVED.includes(value)}>{TASK_LABELS[value]}{RESERVED.includes(value) ? " · future release" : ""}</option>)}
    </select></label></div>
    <fieldset className="workload-fieldset" disabled={!admin || save.isPending}><legend>{TASK_LABELS[task]}</legend>
      <div className="workload-toggle"><Toggle label="Configure placement for this task" checked={configured} onChange={checked => {
        if (checked) update(emptyRoute(status.source_node_id));
        else { const routes = { ...draft.routes }; delete routes[task]; setDraft({ ...draft, routes }); setDirty(true); }
      }} /><span>Configure this task</span></div>
      {!configured && <p className="workload-help">This task keeps its existing configuration until you add and save a route.</p>}
      {configured && <>
        <div className="workload-form-grid">
          <label className="tl-field"><span className="microlabel">Selection</span><select className="tl-select" aria-label="Worker selection mode" value={rule.mode} onChange={event => update({ ...rule, mode: event.target.value as "manual" | "auto" })}><option value="manual">Choose a worker</option><option value="auto">Auto from approved workers</option></select></label>
          {rule.mode === "manual" && <WorkerSelect label="Requested worker" value={rule.target} onChange={target => update({ ...rule, target })} workers={workers} providers={providers} task={task} />}
          <label className="tl-field"><span className="microlabel">Model override (optional)</span><input className="tl-input" aria-label="Placement model override" maxLength={256} value={rule.target?.model ?? ""} onChange={event => update({ ...rule, target: { ...(rule.target ?? { node_id: status.source_node_id, provider_id: null }), model: event.target.value } })} /></label>
        </div>
        {rule.mode === "manual" && selected && <p className="workload-help">{taskStatus?.detail ?? "Task availability not reported."} {selected.cpu_threads} CPU threads · {fmtBytes(selected.memory_bytes)} memory · {selected.queued} queued · {selected.running} running.</p>}
        {rule.mode === "auto" && <fieldset className="workload-fieldset"><legend>Approved Auto workers</legend><p className="workload-help">Only ready workers within the requested budget can be selected. Unchecked and offline workers remain ineligible until they report ready.</p>
          {!workers.length && <p className="workload-help">No approved worker identities are reported.</p>}
          {workers.map(worker => <label className="workload-check" key={worker.node_id}><input type="checkbox" checked={rule.approved_node_ids.includes(worker.node_id)} onChange={event => update({ ...rule, approved_node_ids: event.target.checked ? [...rule.approved_node_ids, worker.node_id] : rule.approved_node_ids.filter(id => id !== worker.node_id) })} /><span>{worker.name || worker.node_id} · {worker.online ? worker.tasks.find(item => item.task === task)?.state ?? "not reported" : "offline"}</span></label>)}
          {rule.approved_node_ids.filter(id => !workers.some(worker => worker.node_id === id)).map(id => <label className="workload-check" key={id}><input type="checkbox" checked onChange={() => update({ ...rule, approved_node_ids: rule.approved_node_ids.filter(value => value !== id) })} /><span>{id} · not reported</span></label>)}
        </fieldset>}
        <details className="workload-identities"><summary>Explicit fallback workers ({rule.fallback_targets.length})</summary><p className="workload-help">An empty list permits no alternate worker. Adding a fallback can move work to that worker if the requested route fails.</p>
          {rule.fallback_targets.map((target, index) => <div className="workload-heading" key={`${targetKey(target)}/${index}`}><span>{workers.find(worker => worker.node_id === target.node_id)?.name || providers.find(provider => provider.provider_id === target.provider_id)?.name || targetKey(target)}{target.model ? ` · ${target.model}` : ""}</span><Button variant="ghost" onClick={() => update({ ...rule, fallback_targets: rule.fallback_targets.filter((_, item) => item !== index) })}>Remove fallback {index + 1}</Button></div>)}
          <div className="workload-form-grid"><WorkerSelect label="New fallback worker" value={fallback} onChange={setFallback} workers={workers} providers={providers} task={task} /><Button variant="outline" disabled={!fallback || rule.fallback_targets.length >= 8 || rule.fallback_targets.some(target => targetKey(target) === targetKey(fallback)) || targetKey(fallback) === targetKey(rule.target)} onClick={() => { if (fallback) update({ ...rule, fallback_targets: [...rule.fallback_targets, fallback] }); setFallback(null); }}>Add selected fallback</Button></div>
        </details>
        <WorkloadBudgetEditor value={rule.budget} onChange={budget => update({ ...rule, budget })} label="Task resource limits" />
      </>}
    </fieldset>
    {dirty && status.policy.revision !== draft.revision && <WorkloadNotice error>The saved policy changed while you were editing. Your draft is preserved; reload to replace it with the current revision.</WorkloadNotice>}
    {problem && <WorkloadNotice error>{problem}</WorkloadNotice>}
    {save.isError && <WorkloadNotice error>{workloadError(save.error)}</WorkloadNotice>}
    {notice && <WorkloadNotice>{notice}</WorkloadNotice>}
    <div className="workload-actions"><Button variant="primary" disabled={!admin || !dirty || !!problem || save.isPending} onClick={async () => { try { const result = await save.mutateAsync({ expected_revision: draft.revision, policy: draft }); setDraft(structuredClone(result.policy)); setDirty(false); setNotice("Placement saved for new work."); } catch { /* Preserve draft. */ } }}>Save placement</Button>
      <Button variant="outline" disabled={latest.isFetching || save.isPending} onClick={async () => { const result = await latest.refetch(); if (result.data) { setDraft(structuredClone(result.data.policy)); setDirty(false); save.reset(); setNotice("Loaded the saved policy."); } }}>Reload saved placement</Button></div>
  </div>;
}

function ProviderEditor({ providers, admin }: { providers: WorkloadProvider[]; admin: boolean }) {
  const [name, setName] = useState(""); const [url, setUrl] = useState(""); const [model, setModel] = useState("");
  const [tasks, setTasks] = useState<WorkloadTask[]>(["motion_description", "printer_analysis"]);
  const [id, setId] = useState(() => crypto.randomUUID());
  const [notice, setNotice] = useState("");
  const save = useSaveWorkloadProvider();
  return <div className="panel"><h2 className="panel-title">Standalone model endpoints</h2><p className="workload-help">Register an Ollama endpoint without treating it as a TailCam node. Registration records its configuration; it does not certify model availability.</p>
    {!providers.length && <p className="workload-help">No model endpoints registered.</p>}
    {providers.map(provider => <div className="workload-stage" key={provider.provider_id}><div className="workload-heading"><b>{provider.name}</b><span className="badge">{provider.enabled ? "Enabled" : "Disabled"}</span></div><p className="workload-help">{provider.base_url} · {provider.model} · {provider.tasks.map(task => TASK_LABELS[task]).join(", ")}</p>{admin && <Button variant="outline" disabled={save.isPending} onClick={() => save.mutate({ ...provider, enabled: !provider.enabled })}>{provider.enabled ? "Disable" : "Enable"} {provider.name}</Button>}</div>)}
    {admin && <details className="workload-identities"><summary>Register a model endpoint</summary><fieldset className="workload-fieldset" disabled={save.isPending}><legend>New endpoint</legend><div className="workload-form-grid">
      <label className="tl-field"><span className="microlabel">Name</span><input className="tl-input" aria-label="Endpoint name" maxLength={128} value={name} onChange={event => setName(event.target.value)} /></label>
      <label className="tl-field"><span className="microlabel">Base URL</span><input className="tl-input" aria-label="Endpoint base URL" type="url" maxLength={2048} value={url} onChange={event => setUrl(event.target.value)} placeholder="http://model-host:11434" /></label>
      <label className="tl-field"><span className="microlabel">Installed model</span><input className="tl-input" aria-label="Endpoint model" maxLength={256} value={model} onChange={event => setModel(event.target.value)} /></label>
    </div><p className="workload-help">Use a plain HTTP(S) endpoint without embedded credentials, query parameters or a path.</p>
    {(["live_detection", "motion_description", "printer_analysis", "labeling"] as WorkloadTask[]).map(task => <label className="workload-check" key={task}><input type="checkbox" checked={tasks.includes(task)} onChange={event => setTasks(current => event.target.checked ? [...current, task] : current.filter(item => item !== task))} /><span>{TASK_LABELS[task]}</span></label>)}
    <div className="workload-actions"><Button variant="primary" disabled={!name.trim() || !url.trim() || !model.trim() || !tasks.length} onClick={async () => { try { await save.mutateAsync({ provider_id: id, kind: "ollama", name: name.trim(), base_url: url.trim(), model: model.trim(), tasks, enabled: true }); setId(crypto.randomUUID()); setName(""); setUrl(""); setModel(""); setNotice("Model endpoint registered."); } catch { /* Keep entered configuration. */ } }}>Register endpoint</Button></div></fieldset></details>}
    {save.isError && <WorkloadNotice error>{workloadError(save.error)}</WorkloadNotice>}{notice && <WorkloadNotice>{notice}</WorkloadNotice>}
  </div>;
}
