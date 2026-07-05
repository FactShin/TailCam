import { useNavigate } from "react-router-dom";

import { useMcp, useUpdateMcp } from "../api/hooks";
import { useCopy, useToast } from "../components/toast";
import { Button, Spinner, Toggle } from "../components/ui";
import { IconBook, IconChip, IconCopy, IconGlobe, IconServer } from "../icons";
import type { McpInfo } from "../types";

/** A code block with a copy button — the workhorse of this screen. */
function CodeBlock({ label, code }: { label: string; code: string }) {
  const copy = useCopy();
  return (
    <div className="mcp-code">
      <div className="mcp-code-head">
        <span className="microlabel">{label}</span>
        <button className="mcp-copy" onClick={() => copy(code, label)} aria-label={`Copy ${label}`}>
          <IconCopy size={13} /> copy
        </button>
      </div>
      <pre className="mono">{code}</pre>
    </div>
  );
}

function snippets(info: McpInfo) {
  const remote = info.http_url_tailnet || "https://<your-node>.your-tailnet.ts.net:8443/mcp";
  // Derive command/args from the backend so a CLI rename can't leave these
  // snippets advertising a stale invocation. stdio_command is "tailcam mcp stdio".
  const parts = info.stdio_command.split(" ");
  const cmd = parts[0];
  const args = info.stdio_args.length ? info.stdio_args : parts.slice(1);
  const argsJson = JSON.stringify(args);
  // Recommended starter tool set (read + incident, no writes), indented for JSON.
  const autoEnable = info.recommended_tools
    .map((t) => `        "${t}"`)
    .join(",\n");
  return {
    claudeCodeLocal:
      `claude mcp add tailcam -e TAILCAM_URL=${info.tailcam_url} -- ${info.stdio_command}`,
    claudeCodeRemote: `claude mcp add --transport http tailcam ${remote}`,
    claudeDesktop: `{
  "mcpServers": {
    "tailcam": {
      "type": "stdio",
      "command": "${cmd}",
      "args": ${argsJson},
      "env": { "TAILCAM_URL": "${info.tailcam_url}" }
    }
  }
}`,
    codex: `# ~/.codex/config.toml — local node over stdio
[mcp_servers.tailcam]
command = "${cmd}"
args = ${argsJson}
env = { TAILCAM_URL = "${info.tailcam_url}" }
default_tools_approval_mode = "prompt"
tool_timeout_sec = 60

# Remote over Tailscale Serve (needs the network endpoint enabled above):
# [mcp_servers.tailcam]
# url = "${remote}"`,
    openclaw: `{
  "mcpServers": {
    "tailcam": {
      "transport": "stdio",
      "command": "${cmd}",
      "args": ${argsJson},
      "env": { "TAILCAM_URL": "${info.tailcam_url}" },
      "autoEnableTools": [
${autoEnable}
      ]
    },
    "tailcam-remote": {
      "transport": "streamable-http",
      "url": "${remote}"
    }
  }
}`,
    hermes: `{
  "mcpServers": {
    "tailcam": {
      "transport": "streamable-http",
      "url": "${remote}"
    }
  }
}`,
  };
}

export function McpSetup() {
  const q = useMcp();
  const update = useUpdateMcp();
  const toast = useToast();
  const copy = useCopy();
  const navigate = useNavigate();
  const info = q.data;

  const set = async (body: { enabled?: boolean; http_enabled?: boolean }, msg: string) => {
    try {
      await update.mutateAsync(body);
      toast.ok(msg);
    } catch {
      toast.err("Could not update MCP settings");
    }
  };

  if (!info) {
    return <div className="screen"><div className="empty"><Spinner size={24} /></div></div>;
  }
  const snip = snippets(info);

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Agent access</span></div>
          <h1 className="screen-title">MCP</h1>
          <p className="screen-sub">
            Let AI agents — Claude Code, Codex, OpenClaw, Hermes — see and operate your cameras
            through the Model Context Protocol. {info.tools_count} tools, role-gated and audited.
          </p>
        </div>
        <div className="head-actions">
          <span className={`ais-status ${info.http_live ? "on" : info.enabled ? "warn" : ""}`}>
            <span className="dot" /> {info.http_live ? "Network live" : info.enabled ? "stdio only" : "Off"}
          </span>
        </div>
      </div>

      {/* Endpoints + toggles */}
      <div className="panel">
        <div className="panel-title"><IconServer size={16} /> This node's endpoints</div>
        <div className="mcp-toggles">
          <label className="ctl-row">
            <span className="ctl-row-label">MCP server (stdio always available when on)</span>
            <Toggle
              checked={info.enabled}
              label="MCP server"
              onChange={(v) => set({ enabled: v }, v ? "MCP on" : "MCP off")}
            />
          </label>
          <label className="ctl-row">
            <span className="ctl-row-label">
              Network endpoint <span className="mono lit">/mcp</span> — for agents on other
              machines (Tailscale-verified callers only; takes effect immediately)
            </span>
            <Toggle
              checked={info.http_enabled}
              label="Network endpoint"
              onChange={(v) => set({ http_enabled: v }, v ? "/mcp endpoint live" : "/mcp endpoint off")}
            />
          </label>
        </div>

        <div className="mcp-endpoints">
          <div className="mcp-ep">
            <span className="microlabel">MCP URL (tailnet · private)</span>
            {info.http_url_tailnet ? (
              <button className="mcp-url mono" onClick={() => copy(info.http_url_tailnet, "MCP URL")}>
                {info.http_url_tailnet} <IconCopy size={13} />
              </button>
            ) : (
              <span className="mcp-url-missing">
                {info.tailscale_running
                  ? "waiting for Tailscale Serve (HTTPS) — check Settings → Tailscale"
                  : "Tailscale isn't running on this node"}
              </span>
            )}
          </div>
          <div className="mcp-ep">
            <span className="microlabel">MCP URL (this machine)</span>
            <button className="mcp-url mono" onClick={() => copy(info.http_url_local, "Local MCP URL")}>
              {info.http_url_local} <IconCopy size={13} />
            </button>
          </div>
          <div className="mcp-ep">
            <span className="microlabel">stdio command (local agents)</span>
            <button className="mcp-url mono" onClick={() => copy(info.stdio_command, "Command")}>
              {info.stdio_command} <IconCopy size={13} />
            </button>
          </div>
        </div>
        {!info.http_live && (
          <p className="ais-intro" style={{ marginTop: 10 }}>
            The network URLs answer only while the toggles above are on. stdio works whenever the
            MCP server is on and TailCam is running on the same machine as the agent.
          </p>
        )}
      </div>

      {/* Client setup */}
      <div className="panel">
        <div className="panel-title"><IconChip size={16} /> Connect your agent</div>
        <p className="ais-intro">
          Pick your agent and paste — every snippet below is already filled in with this node's
          real URLs. Local agents use stdio (no network setup at all); remote agents use the
          tailnet URL (enable the network endpoint above).
        </p>

        <div className="mcp-clients">
          <div className="mcp-client">
            <h3>Claude Code</h3>
            <CodeBlock label="Terminal — same machine" code={snip.claudeCodeLocal} />
            <CodeBlock label="Terminal — remote node over tailnet" code={snip.claudeCodeRemote} />
          </div>

          <div className="mcp-client">
            <h3>Codex</h3>
            <CodeBlock label="~/.codex/config.toml" code={snip.codex} />
          </div>

          <div className="mcp-client">
            <h3>OpenClaw</h3>
            <CodeBlock label="mcpServers block — stdio + remote" code={snip.openclaw} />
          </div>

          <div className="mcp-client">
            <h3>Hermes</h3>
            <CodeBlock label="mcpServers block — remote over tailnet" code={snip.hermes} />
          </div>

          <div className="mcp-client">
            <h3>Claude Desktop</h3>
            <CodeBlock label="claude_desktop_config.json" code={snip.claudeDesktop} />
          </div>
        </div>
      </div>

      {/* What agents can do + security */}
      <div className="panel">
        <div className="panel-title"><IconGlobe size={16} /> What agents get — and what protects you</div>
        <div className="mcp-facts">
          <div>
            <b>{info.tools_count} tools</b>
            <span>cameras, snapshots, recordings, motion events, AI, timelapse, fleet health, guarded admin workflows.</span>
          </div>
          <div>
            <b>Tailscale identity</b>
            <span>network callers must be verified tailnet members; your Tailscale ACLs decide who can reach the port at all.</span>
          </div>
          <div>
            <b>Roles</b>
            <span>viewer → operator → admin; destructive and fleet-wide actions additionally require an explicit confirmation string.</span>
          </div>
          <div>
            <b>Audit log</b>
            <span>every state-changing tool call is recorded with the real caller identity.</span>
          </div>
        </div>
      </div>

      <div className="panel ais-train">
        <div className="ais-train-ic"><IconBook size={20} /></div>
        <div className="ais-train-text">
          <b>Want the deep dive?</b>
          <span>Tool-by-tool reference, security model, and troubleshooting live in the docs.</span>
        </div>
        <Button variant="ghost" onClick={() => navigate("/docs/mcp-overview")}>
          MCP docs →
        </Button>
      </div>
    </div>
  );
}
