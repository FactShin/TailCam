import { useState } from "react";

import { useCameras, useHosts, useSystem } from "../api/hooks";
import { IntegrationsPanel } from "../components/IntegrationsPanel";
import { NotificationsSettings } from "../components/NotificationsSettings";
import { NodePurposePanel } from "../components/NodePurposePanel";
import { NodeReadinessPanel } from "../components/NodeReadinessPanel";
import { StoragePanel } from "../components/StoragePanel";
import { StreamingPanel } from "../components/StreamingPanel";
import { useToast } from "../components/toast";
import { IconCheck, IconCopy, IconDevice, IconInfo, IconServer, IconWifi, IconWifiOff } from "../icons";
import { copyToClipboard } from "../lib/clipboard";
import { fmtBytes } from "../lib/format";
import { nodeRoleSummary } from "../lib/nodeRoles";

export function Settings() {
  const sys = useSystem().data;
  const cameras = useCameras().data ?? [];
  const hosts = useHosts().data ?? [];
  const toast = useToast();
  const [copied, setCopied] = useState(false);

  const copy = async (text: string) => {
    // Falls back to execCommand over http:// (no secure context, so
    // navigator.clipboard is undefined) instead of silently failing.
    if (!(await copyToClipboard(text))) {
      toast.err("Copy failed");
      return;
    }
    setCopied(true);
    toast.ok("Copied access URL");
    setTimeout(() => setCopied(false), 1600);
  };

  if (!sys) return <div className="screen"><div className="empty">Loading…</div></div>;

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">System Console</span></div>
          <h1 className="screen-title">Settings</h1>
          <p className="screen-sub">System &amp; access</p>
        </div>
      </div>

      <div className="settings-grid">
        <NodePurposePanel />
        <NodeReadinessPanel />
        <div className="panel">
          <div className="panel-title"><IconInfo size={16} /> System</div>
          <div className="kv"><span className="kv-k">Version</span><span className="kv-v mono">TailCam {sys.version}</span></div>
          <div className="kv"><span className="kv-k">This device</span><span className="kv-v mono">{sys.node_name || sys.host}</span></div>
          {sys.node_name && sys.node_name !== sys.host && <div className="kv"><span className="kv-k">Hostname</span><span className="kv-v mono">{sys.host}</span></div>}
          <div className="kv">
            <span className="kv-k">Hardware</span>
            <span className="kv-v mono">
              {sys.host_model || "generic"} · {sys.ram_gb ? `${sys.ram_gb} GB` : "?"} · {sys.cpu_count} CPU
              {sys.low_power && <span className="badge badge-warn" style={{ marginLeft: 8 }}>low-power profile</span>}
            </span>
          </div>
          {sys.low_power && (
            <span className="help-foot mono">
              Low-power host: lighter stream defaults, object detection off until routed to a bigger node,
              and captures can be sent to a storage node (below).
            </span>
          )}
          <div className="kv"><span className="kv-k">Cameras (all hosts)</span><span className="kv-v mono">{cameras.length} connected</span></div>
          <div className="kv"><span className="kv-k">Storage used (local)</span><span className="kv-v mono">{fmtBytes(sys.media_bytes)}</span></div>
        </div>

        <div className="panel">
          <div className="panel-title"><IconServer size={16} /> Tailnet devices</div>
          {hosts.length === 0 && <div className="kv"><span className="kv-v mono">No nodes discovered.</span></div>}
          {hosts.map((h) => (
            <div className="kv node-device" key={h.node_key}>
              <span className="kv-k node-device-detail">
                <span>{h.node_name || h.host}{h.kind === "local" ? " (this device)" : ""}</span>
                {h.node_name && h.node_name !== h.host && <span className="mono">{h.host}</span>}
                <span className="node-device-roles">{nodeRoleSummary(h.node_roles)}</span>
              </span>
              <span className="kv-v">
                <span className={`badge ${h.online ? "badge-ok" : "badge-err"}`}>
                  <span className="pill-dot" style={{ background: h.online ? "var(--ok)" : "var(--err)" }} />
                  {h.camera_count} cam{h.camera_count !== 1 ? "s" : ""}
                </span>
              </span>
            </div>
          ))}
        </div>

        <div className="panel">
          <div className="panel-title"><IconInfo size={16} /> AI motion analysis</div>
          <p className="ais-intro">
            Configure motion labels, object detection, and models in AI Studio.
            Runtime readiness above shows current task checks.
          </p>
          <a className="btn btn-outline" href="/ai">Open AI Studio</a>
        </div>

        <div className="panel">
          <div className="panel-title">{sys.tailscale_running ? <IconWifi size={16} /> : <IconWifiOff size={16} />} Tailscale</div>
          <div className="kv">
            <span className="kv-k">Status</span>
            <span className="kv-v">
              {sys.tailscale_running ? (
                <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> Running</span>
              ) : sys.tailscale_installed ? (
                <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> Installed · stopped</span>
              ) : (
                <span className="badge badge-err"><span className="pill-dot" style={{ background: "var(--err)" }} /> Not installed</span>
              )}
            </span>
          </div>
          <div className="kv kv-stack">
            <span className="kv-k">Access URL (private)</span>
            <div className="url-row">
              <code className="url-code mono">{sys.access_url}</code>
              <button className="copy-btn" onClick={() => copy(sys.access_url)} aria-label="Copy access URL">
                {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
              </button>
            </div>
          </div>
          <div className="kv kv-stack">
            <span className="kv-k">Local URL</span>
            <code className="url-code mono">{sys.local_url}</code>
          </div>
        </div>

        <StreamingPanel />

        <StoragePanel />

        <NotificationsSettings />

        <IntegrationsPanel />


        <div className="panel panel-help">
          <div className="panel-title"><IconDevice size={16} /> Access from another device</div>
          <ol className="help-list">
            <li>Install <span className="mono">Tailscale</span> on your phone or laptop and sign in to the same tailnet.</li>
            <li>Open the private access URL above in any browser — no password, the network is the boundary.</li>
            <li>Add TailCam to your home screen to install it as an app (fullscreen, offline app-shell).</li>
          </ol>
          <p className="help-foot mono">No accounts · no tokens · no telemetry. Security is handled by Tailscale.</p>
        </div>
      </div>
    </div>
  );
}
