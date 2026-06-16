import { useState } from "react";

import { useFleetNodeCapabilities, useReloadFleetNode } from "../api/hooks";
import { IconBrain, IconCamera, IconHdd, IconRefresh, IconServer, IconWifi, IconWifiOff } from "../icons";
import { fmtBytes } from "../lib/format";
import { nodeHealthSeverity, type FleetNodeSeverity } from "../lib/fleet";
import type { HostInfo, NodeHealthInfo } from "../types";
import { useToast } from "./toast";
import { Button, ConfirmDialog, Spinner } from "./ui";

const severityBadge: Record<FleetNodeSeverity, { label: string; cls: string; dot: string }> = {
  healthy: { label: "Healthy", cls: "badge-ok", dot: "var(--ok)" },
  warning: { label: "Warning", cls: "badge-warn", dot: "var(--warn)" },
  error: { label: "Error", cls: "badge-err", dot: "var(--err)" },
  offline: { label: "Offline", cls: "badge-err", dot: "var(--err)" },
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Health relay failed";
}

function uptime(seconds: number | undefined): string {
  if (seconds == null) return "unknown";
  const s = Math.max(0, Math.floor(seconds));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const mins = Math.floor((s % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="fleet-metric">
      <span className="microlabel">{label}</span>
      <span className="fleet-metric-v mono">{value}</span>
    </div>
  );
}

export function NodeHealthCard({
  host,
  health,
  healthLoading,
  healthError,
  currentVersion,
}: {
  host: HostInfo;
  health?: NodeHealthInfo;
  healthLoading?: boolean;
  healthError?: unknown;
  currentVersion?: string | null;
}) {
  const toast = useToast();
  const [confirmReload, setConfirmReload] = useState(false);
  const caps = useFleetNodeCapabilities(host.node_key, host.online);
  const reload = useReloadFleetNode();
  const severity = nodeHealthSeverity(host, health, currentVersion, healthError);
  const badge = severityBadge[severity];
  const roles = caps.data?.principal.roles ?? [];
  const version = health?.version ?? host.version ?? "unknown";
  const versionDrift = Boolean(currentVersion && health?.version && health.version !== currentVersion);
  const storage = (health?.media_bytes ?? 0) + (health?.timelapse_bytes ?? 0);

  const doReload = async () => {
    setConfirmReload(false);
    try {
      await reload.mutateAsync(host.node_key);
      toast.ok(`Reloaded ${host.host}`);
    } catch (err) {
      toast.err(err instanceof Error ? err.message : "Reload failed");
    }
  };

  return (
    <article className={`fleet-card fleet-card-${severity}`}>
      <header className="fleet-card-head">
        <div className="fleet-node-title">
          <span className="fleet-node-icon"><IconServer size={18} /></span>
          <div>
            <h2>{host.host}</h2>
            <span className="fleet-node-sub mono">{host.kind === "local" ? "local" : host.node_key}</span>
          </div>
        </div>
        <span className={`badge ${badge.cls}`}>
          <span className="pill-dot" style={{ background: badge.dot }} />
          {badge.label}
        </span>
      </header>

      {healthLoading && !health ? (
        <div className="fleet-loading"><Spinner size={16} /> Loading node health...</div>
      ) : healthError ? (
        <div className="fleet-issue fleet-issue-error">{errorMessage(healthError)}</div>
      ) : null}

      <div className="fleet-meta-row">
        <span className={`badge ${versionDrift || health?.update_available ? "badge-warn" : "badge-accent"}`}>
          v{version}{versionDrift ? " drift" : ""}
        </span>
        <span className="fleet-muted mono">{health?.platform ?? "platform unknown"}</span>
        <span className="fleet-muted mono">up {uptime(health?.uptime_seconds)}</span>
      </div>

      <div className="fleet-metrics">
        <Metric label="Cameras" value={health ? `${health.camera_online}/${health.camera_total}` : `${host.camera_count}`} />
        <Metric label="Recording" value={`${health?.camera_recording ?? 0}`} />
        <Metric label="Storage" value={fmtBytes(storage)} />
        <Metric label="Issues" value={`${health?.issues.length ?? (healthError ? 1 : 0)}`} />
      </div>

      <div className="fleet-systems">
        <div className="fleet-system">
          <IconCamera size={15} />
          <span>{health ? `${health.camera_degraded} degraded · ${health.camera_offline} offline` : "camera health unavailable"}</span>
        </div>
        <div className="fleet-system">
          {health?.tailscale_running ? <IconWifi size={15} /> : <IconWifiOff size={15} />}
          <span>{health?.tailscale_running ? (health.tailscale_served ? "Tailscale Serve active" : "Tailscale running") : "Tailscale unavailable"}</span>
        </div>
        <div className="fleet-system">
          <IconBrain size={15} />
          <span>
            {health?.ai_enabled
              ? health.ai_reachable && health.ai_model_present
                ? `AI ready · ${health.ai_model}`
                : `AI attention · ${health.ai_model}`
              : "AI disabled"}
          </span>
        </div>
        <div className="fleet-system">
          <IconHdd size={15} />
          <span>{health?.access_url ?? "access URL unavailable"}</span>
        </div>
      </div>

      <div className="fleet-auth">
        <span className="microlabel">Authorization</span>
        {caps.isLoading ? (
          <span className="fleet-muted mono">checking...</span>
        ) : caps.isError ? (
          <span className="fleet-muted mono">unavailable</span>
        ) : caps.data ? (
          <span className="fleet-muted mono">
            {caps.data.principal.source} · {roles.length ? roles.join("/") : "no roles"}
          </span>
        ) : (
          <span className="fleet-muted mono">not checked</span>
        )}
      </div>

      {health?.issues.length ? (
        <div className="fleet-issues">
          {health.issues.slice(0, 3).map((issue) => (
            <div key={`${issue.code}:${issue.summary}`} className={`fleet-issue fleet-issue-${issue.severity}`}>
              <span className="mono">{issue.code}</span>
              <span>{issue.summary}</span>
            </div>
          ))}
          {health.issues.length > 3 && <span className="fleet-muted mono">+{health.issues.length - 3} more issue(s)</span>}
        </div>
      ) : null}

      <footer className="fleet-card-foot">
        <Button
          variant="outline"
          size="sm"
          icon={<IconRefresh size={14} className={reload.isPending ? "spin" : ""} />}
          disabled={!host.online || reload.isPending}
          onClick={() => setConfirmReload(true)}
        >
          Reload node
        </Button>
        <span className="fleet-muted mono">{host.proxy_prefix || "/local"}</span>
      </footer>

      <ConfirmDialog
        open={confirmReload}
        title={`Reload ${host.host}?`}
        body="This restarts capture workers and re-discovers cameras on that TailCam node."
        confirmLabel={reload.isPending ? "Reloading..." : "Reload"}
        danger={false}
        onCancel={() => setConfirmReload(false)}
        onConfirm={doReload}
      />
    </article>
  );
}
