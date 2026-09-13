import type { WorkloadBudget } from "../workloadTypes";
import { Toggle } from "./ui";

const MIB = 1024 ** 2;
export const defaultWorkloadBudget = (): WorkloadBudget => ({ cpu_threads: 1, cpu_seconds: 3600,
  memory_bytes: 512 * MIB, gpu_slots: 0, workspace_bytes: 256 * MIB, output_bytes: 128 * MIB,
  wall_seconds: 3600, cancel_grace_seconds: 3, require_hard_memory_limit: false });

export function workloadBudgetError(budget: WorkloadBudget): string | null {
  const integers: [keyof WorkloadBudget, number, number][] = [
    ["cpu_threads", 1, 256], ["cpu_seconds", 1, 604800], ["memory_bytes", 16 * MIB, 2 ** 50],
    ["gpu_slots", 0, 1], ["workspace_bytes", 1, 2 ** 50], ["output_bytes", 1, 2 ** 50],
  ];
  if (integers.some(([key, minimum, maximum]) => {
    const value = budget[key];
    return typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum;
  })) return "Use finite positive resource limits within the displayed ranges.";
  if (!Number.isFinite(budget.wall_seconds) || budget.wall_seconds <= 0 || budget.wall_seconds > 604800) return "Wall time must be between 1 second and 7 days.";
  if (budget.cpu_seconds < budget.wall_seconds * budget.cpu_threads) return "CPU allowance must cover CPU threads × wall time; a tighter CPU-time limit is unavailable.";
  if (!Number.isFinite(budget.cancel_grace_seconds) || budget.cancel_grace_seconds < 0 || budget.cancel_grace_seconds > 30) return "Stop grace time must be between 0 and 30 seconds.";
  return null;
}

export function WorkloadBudgetEditor({ value, onChange, label = "Resource limits", disabled = false }: {
  value: WorkloadBudget; onChange: (budget: WorkloadBudget) => void; label?: string; disabled?: boolean;
}) {
  const fields: { key: keyof WorkloadBudget; name: string; minimum: number; maximum: number; unit?: number }[] = [
    { key: "cpu_threads", name: "CPU threads", minimum: 1, maximum: 256 },
    { key: "cpu_seconds", name: "CPU allowance (thread-seconds)", minimum: 1, maximum: 604800 },
    { key: "wall_seconds", name: "Wall time (seconds)", minimum: 1, maximum: 604800 },
    { key: "memory_bytes", name: "Memory reservation (MiB)", minimum: 16, maximum: 2 ** 30, unit: MIB },
    { key: "workspace_bytes", name: "Temporary workspace (MiB)", minimum: 1, maximum: 2 ** 30, unit: MIB },
    { key: "output_bytes", name: "Committed outputs (MiB)", minimum: 1, maximum: 2 ** 30, unit: MIB },
    { key: "cancel_grace_seconds", name: "Stop grace (seconds)", minimum: 0, maximum: 30 },
  ];
  return <fieldset className="workload-fieldset" disabled={disabled}><legend>{label}</legend>
    <p className="workload-help">Workers validate these limits before accepting work. A requirement they cannot enforce may make that worker unavailable.</p>
    <p className="workload-help">CPU allowance reserves threads × wall time. It is an admission estimate; actual CPU consumption is not metered.</p>
    <div className="workload-form-grid">{fields.map(field => <label className="tl-field" key={field.key}><span className="microlabel">{field.name}</span>
      <input className="tl-input" aria-label={`${label}: ${field.name}`} type="number" min={field.minimum} max={field.maximum} step={1} value={Number(value[field.key]) / (field.unit ?? 1)} onChange={event => onChange({ ...value, [field.key]: Number(event.target.value) * (field.unit ?? 1) })} />
    </label>)}
      <label className="tl-field"><span className="microlabel">GPU</span><select className="tl-select" aria-label={`${label}: GPU`} value={value.gpu_slots} onChange={event => onChange({ ...value, gpu_slots: Number(event.target.value) })}><option value={0}>No GPU required</option><option value={1}>Reserve one GPU slot</option></select></label>
    </div>
    <div className="workload-toggle"><Toggle label={`${label}: Require hard memory limit`} checked={value.require_hard_memory_limit} onChange={checked => onChange({ ...value, require_hard_memory_limit: checked })} /><span>Require hard memory limit</span></div>
    <p className="workload-help">Memory reserves admission capacity; actual process memory is not capped. Enable the hard-limit requirement to reject workers without a memory ceiling. A reported GPU slot does not prove a model fits that GPU.</p>
  </fieldset>;
}
