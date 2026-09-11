import { useState } from "react";

import { ApiError } from "../api/client";
import { useNodeConfig, useUpdateNodeConfig } from "../api/hooks";
import { IconServer } from "../icons";
import { NODE_ROLE_LABELS, nodeRoleSummary } from "../lib/nodeRoles";
import type { NodeConfigUpdate, NodeRole } from "../types";
import { useToast } from "./toast";
import { Button } from "./ui";

const ROLES: { id: NodeRole; description: string }[] = [
  { id: "capture", description: "Discover attached cameras and publish live frames." },
  { id: "storage", description: "Save snapshots, recordings, and timelapses on this device." },
  { id: "analysis", description: "Run detection and AI analysis here when configured." },
  { id: "training", description: "Collect datasets and train models when configured." },
];

const PRESETS: { id: string; label: string; roles: NodeRole[] }[] = [
  { id: "all-in-one", label: "All-in-one", roles: ["capture", "storage", "analysis", "training"] },
  { id: "camera", label: "Camera with local storage", roles: ["capture", "storage"] },
  { id: "hub", label: "Hub · view and control", roles: [] },
  { id: "storage", label: "Storage server", roles: ["storage"] },
  { id: "compute", label: "AI and training worker", roles: ["analysis", "training"] },
];

function sameRoles(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((role) => b.includes(role));
}

export function NodePurposePanel() {
  const query = useNodeConfig();
  const update = useUpdateNodeConfig();
  const toast = useToast();
  const [draft, setDraft] = useState<NodeConfigUpdate | null>(null);
  const data = query.data;

  const title = <h2 className="panel-title" id="node-purpose-title"><IconServer size={16} /> Node purpose</h2>;
  if (!data) {
    const legacy = query.error instanceof ApiError && query.error.status === 404;
    return (
      <section className="panel node-purpose" aria-labelledby="node-purpose-title">
        {title}
        {query.isPending ? <p className="ais-intro" role="status">Loading node purpose…</p> : (
          <>
            <p className="ais-intro" role="alert">
              {legacy ? "Update this device to configure its node purpose." : "Could not load this device’s node purpose."}
            </p>
            <Button onClick={() => query.refetch()} disabled={query.isFetching}>Retry</Button>
          </>
        )}
      </section>
    );
  }

  const name = draft?.name ?? data.name;
  const roles = draft?.roles ?? data.configured_roles;
  const dirty = name !== data.name || !sameRoles(roles, data.configured_roles);
  const preset = PRESETS.find((item) => sameRoles(item.roles, roles))?.id ?? "custom";
  const edit = (changes: NodeConfigUpdate) => {
    update.reset();
    setDraft({ ...draft, ...changes });
  };
  const save = async () => {
    try {
      // Keep independently edited fields independent: renaming must not
      // overwrite role changes made by another administrator during editing.
      const body: NodeConfigUpdate = {};
      if (draft?.name !== undefined && name !== data.name) body.name = name.trim();
      if (draft?.roles !== undefined && !sameRoles(roles, data.configured_roles)) body.roles = roles;
      const result = await update.mutateAsync(body);
      setDraft(null);
      toast.ok(result.restart_required ? "Node purpose saved. Restart TailCam to apply roles." : "Node purpose saved");
    } catch {
      // Keep the draft and explain the failure next to the save action.
    }
  };
  const saveError = update.error instanceof ApiError && update.error.status === 403
    ? "Admin access is required to change node purpose. Open this device locally or use an authorized Tailscale account."
    : update.error instanceof Error ? update.error.message : "Could not save node purpose. Try again.";

  return (
    <section className="panel node-purpose" aria-labelledby="node-purpose-title">
      {title}
      <p className="ais-intro">
        Choose what runs on this device. Every node can view and control the fleet.
        Role changes take effect after restarting TailCam; feature settings are kept.
      </p>
      <form onSubmit={(event) => { event.preventDefault(); if (dirty && !update.isPending) void save(); }}>
        <fieldset className="node-purpose-fields" disabled={update.isPending}>
          <div className="node-purpose-grid">
            <label className="tl-field">
              <span className="microlabel">Device name</span>
              <input className="tl-input" value={name} maxLength={64} placeholder="Use hostname"
                onChange={(event) => edit({ name: event.target.value })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Purpose preset</span>
              <select className="tl-select" value={preset} onChange={(event) => {
                const selected = PRESETS.find((item) => item.id === event.target.value);
                if (selected) edit({ roles: [...selected.roles] });
              }}>
                {PRESETS.map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}
                <option value="custom">Custom</option>
              </select>
            </label>
          </div>
          <div className="node-role-options" role="group" aria-label="Device workload roles">
            {ROLES.map((role) => (
              <label className="node-role-option" key={role.id}>
                <input type="checkbox" checked={roles.includes(role.id)} aria-label={NODE_ROLE_LABELS[role.id]}
                  onChange={(event) => edit({ roles: event.target.checked ? [...roles, role.id] : roles.filter((id) => id !== role.id) })} />
                <span><strong>{NODE_ROLE_LABELS[role.id]}</strong><span>{role.description}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="node-purpose-current">
          <span className="microlabel">Enabled now</span>
          <p>{nodeRoleSummary(data.active_roles)}</p>
          <span className="node-purpose-note">Models, runtime support, and storage settings determine which jobs are ready.</span>
        </div>
        {data.restart_required && (
          <div className="node-purpose-restart" role="status">
            <strong>Restart required</strong>
            <p>Saved for the next start: {nodeRoleSummary(data.configured_roles)}.</p>
            <p>Run <code>tailcam restart</code> on this device, or restart its Docker container. Plan around active recordings and jobs.</p>
          </div>
        )}
        {query.isError && <p className="node-purpose-error" role="alert">Could not refresh node purpose. Showing the last saved state.</p>}
        {update.isError && <p className="node-purpose-error" role="alert">{saveError}</p>}
        <div className="node-purpose-actions">
          <Button type="submit" variant="primary" disabled={!dirty || update.isPending}>
            {update.isPending ? "Saving…" : "Save purpose"}
          </Button>
          {dirty && <Button type="button" disabled={update.isPending} onClick={() => { setDraft(null); update.reset(); }}>Discard changes</Button>}
        </div>
      </form>
      <details className="node-purpose-identity">
        <summary>Persistent node ID</summary>
        <code>{data.node_id}</code>
      </details>
    </section>
  );
}
