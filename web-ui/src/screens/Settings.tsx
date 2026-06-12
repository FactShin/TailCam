import { useState } from "react";

import { useAi, useCameras, useHosts, useSystem } from "../api/hooks";
import { useToast } from "../components/toast";
import { IconCheck, IconCopy, IconDevice, IconInfo, IconServer, IconWifi, IconWifiOff } from "../icons";
import { fmtBytes } from "../lib/format";

export function Settings() {
  const sys = useSystem().data;
  const cameras = useCameras().data ?? [];
  const hosts = useHosts().data ?? [];
  const ai = useAi().data;
  const toast = useToast();
  const [copied, setCopied] = useState(false);

  const copy = (text: string) => {
    try {
      navigator.clipboard.writeText(text);
    } catch {
      /* ignore */
    }
    setCopied(true);
    toast.ok("Copied access URL");
    setTimeout(() => setCopied(false), 1600);
  };

  if (!sys) return <div className="screen"><div className="empty">Loading…</div></div>;

  return (
    <div className="screen">
      <div className="screen-head"><div><h1 className="screen-title">Settings</h1><p className="screen-sub">System &amp; access</p></div></div>

      <div className="settings-grid">
        <div className="panel">
          <div className="panel-title"><IconInfo size={16} /> System</div>
          <div className="kv"><span className="kv-k">Version</span><span className="kv-v mono">AnyCam {sys.version}</span></div>
          <div className="kv"><span className="kv-k">This device</span><span className="kv-v mono">{sys.host}</span></div>
          <div className="kv"><span className="kv-k">Cameras (all hosts)</span><span className="kv-v mono">{cameras.length} connected</span></div>
          <div className="kv"><span className="kv-k">Storage used (local)</span><span className="kv-v mono">{fmtBytes(sys.media_bytes)}</span></div>
        </div>

        <div className="panel">
          <div className="panel-title"><IconServer size={16} /> Tailnet devices</div>
          {hosts.length === 0 && <div className="kv"><span className="kv-v mono">No nodes discovered.</span></div>}
          {hosts.map((h) => (
            <div className="kv" key={h.host}>
              <span className="kv-k">{h.host}{h.kind === "local" ? " (this device)" : ""}</span>
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
          {!ai || !ai.enabled ? (
            <div className="kv kv-stack">
              <span className="kv-k">Status</span>
              <span className="kv-v">
                <span className="badge"><span className="pill-dot" style={{ background: "var(--muted)" }} /> Disabled</span>
              </span>
              <span className="help-foot mono">
                Enable in config.toml [ai]: set enabled=true and point base_url at an Ollama host
                (e.g. your Mac mini). Motion events then get labels (person/animal/vehicle…).
              </span>
            </div>
          ) : (
            <>
              <div className="kv"><span className="kv-k">Model</span><span className="kv-v mono">{ai.model}</span></div>
              <div className="kv">
                <span className="kv-k">Ollama</span>
                <span className="kv-v">
                  {ai.reachable && ai.model_present ? (
                    <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> Ready</span>
                  ) : ai.reachable ? (
                    <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> Model not pulled</span>
                  ) : (
                    <span className="badge badge-err"><span className="pill-dot" style={{ background: "var(--err)" }} /> Unreachable</span>
                  )}
                </span>
              </div>
              {ai.reachable && !ai.model_present && (
                <span className="help-foot mono">Run on the Ollama host: ollama pull {ai.model}</span>
              )}
            </>
          )}
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

        <div className="panel panel-help">
          <div className="panel-title"><IconDevice size={16} /> Access from another device</div>
          <ol className="help-list">
            <li>Install <span className="mono">Tailscale</span> on your phone or laptop and sign in to the same tailnet.</li>
            <li>Open the private access URL above in any browser — no password, the network is the boundary.</li>
            <li>Add AnyCam to your home screen to install it as an app (fullscreen, offline app-shell).</li>
          </ol>
          <p className="help-foot mono">No accounts · no tokens · no telemetry. Security is handled by Tailscale.</p>
        </div>
      </div>
    </div>
  );
}
