import { useQueries } from "@tanstack/react-query";

import { fleetNodeHealthQueryOptions, useHosts, useSystem } from "../api/hooks";
import { NodeHealthCard } from "../components/NodeHealthCard";
import { IconRefresh, IconServer } from "../icons";
import { summarizeFleetHealth } from "../lib/fleet";

export function Fleet() {
  const hostsQ = useHosts();
  const system = useSystem().data;
  const hosts = hostsQ.data ?? [];
  const healthQueries = useQueries({
    queries: hosts.map((host) => fleetNodeHealthQueryOptions(host.node_key, host.online)),
  });
  const summary = summarizeFleetHealth(
    hosts.map((host, i) => ({
      host,
      health: healthQueries[i]?.data,
      error: healthQueries[i]?.error,
    })),
    system?.version,
  );

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Fleet Command</span></div>
          <h1 className="screen-title">Fleet</h1>
          <p className="screen-sub">
            Manage every TailCam node through the fixed Tailscale relay ·{" "}
            {hostsQ.isFetching && <span className="updating"><IconRefresh size={12} className="spin" /> updating...</span>}
          </p>
        </div>
      </div>

      <div className="statstrip fleet-strip">
        <div className="stat">
          <span className="microlabel">Healthy</span>
          <span className="stat-v"><span className="up">{summary.healthy}</span><small>nodes</small></span>
        </div>
        <div className="stat">
          <span className="microlabel">Warnings</span>
          <span className="stat-v">{summary.warning}<small>attention</small></span>
          {summary.warning > 0 && <span className="led warn stat-led" />}
        </div>
        <div className="stat">
          <span className="microlabel">Offline/Error</span>
          <span className="stat-v">{summary.offline + summary.error}<small>nodes</small></span>
          {summary.offline + summary.error > 0 && <span className="led err stat-led" />}
        </div>
        <div className="stat">
          <span className="microlabel">Recording</span>
          <span className="stat-v">{summary.recording}<small>active</small></span>
          {summary.recording > 0 && <span className="led err blink stat-led" />}
        </div>
      </div>

      {hostsQ.isLoading ? (
        <div className="empty">
          <div className="empty-ic"><IconServer size={38} /></div>
          <div className="empty-title">Finding TailCam nodes</div>
          <div className="empty-sub">Discovery is checking the local node and any reachable tailnet peers.</div>
        </div>
      ) : hosts.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconServer size={38} /></div>
          <div className="empty-title">No TailCam nodes found</div>
          <div className="empty-sub">Start TailCam locally or add peers in your tailnet to manage the fleet.</div>
        </div>
      ) : (
        <div className="fleet-grid">
          {hosts.map((host, i) => (
            <NodeHealthCard
              key={host.node_key}
              host={host}
              health={healthQueries[i]?.data}
              healthLoading={healthQueries[i]?.isLoading}
              healthError={healthQueries[i]?.error}
              currentVersion={system?.version}
            />
          ))}
        </div>
      )}
    </div>
  );
}
