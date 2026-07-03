import { useState } from "react";

import { usePluginAction, usePlugins, usePluginsMarket } from "../api/hooks";
import { PluginsPanel } from "../components/PluginsPanel";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, Spinner, Toggle } from "../components/ui";
import { IconAi, IconBell, IconBook, IconChip, IconMotion, IconRefresh, IconTrash } from "../icons";
import type { InstalledPluginEntry, MarketPluginEntry } from "../types";

function kindChip(kind: string) {
  const icon =
    kind === "ai" ? <IconAi size={12} /> :
    kind === "notification" ? <IconBell size={12} /> :
    kind === "event" ? <IconMotion size={12} /> : <IconChip size={12} />;
  return (
    <span key={kind} className="ais-badge ok plug-kind">
      {icon} {kind}
    </span>
  );
}

/** Extend TailCam: browse the curated marketplace, manage installed plugins,
 * and learn how to build your own. */
export function Plugins() {
  const toast = useToast();
  const market = usePluginsMarket();
  const actions = usePluginAction();
  const data = market.data;
  const [confirmRemove, setConfirmRemove] = useState<InstalledPluginEntry | null>(null);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast.ok(ok);
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Plugin action failed");
    }
  };

  const install = (p: MarketPluginEntry) =>
    run(
      () => actions.install.mutateAsync(p.id),
      p.installed ? `${p.name} updated` : `${p.name} installed — it's live now`,
    );

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">Extend TailCam</span></div>
          <h1 className="screen-title">Plugins</h1>
          <p className="screen-sub">
            Add AI providers, notification channels, and event automations — from the community
            marketplace or your own code.
          </p>
        </div>
        <div className="head-actions">
          <Button
            variant="outline"
            icon={<IconRefresh size={14} />}
            onClick={() => run(() => actions.refresh.mutateAsync(), "Marketplace refreshed")}
            disabled={actions.refresh.isPending}
          >
            Refresh
          </Button>
        </div>
      </div>

      {/* Installed */}
      <div className="panel">
        <div className="panel-title"><IconChip size={16} /> Installed on this node</div>
        {market.isLoading ? (
          <div className="empty"><Spinner size={20} /></div>
        ) : (data?.installed.length ?? 0) === 0 ? (
          <p className="ais-intro">
            Nothing installed yet — pick something from the marketplace below, or drop a{" "}
            <span className="mono lit">.py</span> file into your config folder's{" "}
            <span className="mono lit">plugins/</span> directory and hit Refresh.
          </p>
        ) : (
          <div className="plug-list">
            {data!.installed.map((p) => (
              <div key={p.id} className="plug-row">
                <span className={`ais-pipe-dot ${p.loaded ? "ok" : p.enabled ? "warn" : ""}`} />
                <div className="plug-main">
                  <div className="plug-name">
                    {p.id}
                    {p.version && <span className="mono plug-ver"> v{p.version}</span>}
                  </div>
                  <div className="plug-desc mono">
                    {p.file} · {p.source === "market" ? "marketplace" : "manual drop-in"}
                    {!p.enabled ? " · disabled" : p.loaded ? " · running" : " · not loaded"}
                  </div>
                </div>
                <div className="plug-badges">
                  {p.update_available && (
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => run(() => actions.install.mutateAsync(p.market_id), `${p.id} updated to v${p.update_available}`)}
                    >
                      Update to v{p.update_available}
                    </Button>
                  )}
                  <Toggle
                    checked={p.enabled}
                    label={`Enable ${p.id}`}
                    onChange={(v) =>
                      run(
                        () => actions.toggle.mutateAsync({ stem: p.id, enabled: v }),
                        v ? `${p.id} enabled` : `${p.id} disabled`,
                      )
                    }
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<IconTrash size={14} />}
                    onClick={() => setConfirmRemove(p)}
                    aria-label={`Uninstall ${p.id}`}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
        {(data?.load_errors.length ?? 0) > 0 && (
          <div className="plug-errors">
            {data!.load_errors.map((e, i) => <div key={i} className="plug-err">⚠ {e}</div>)}
          </div>
        )}
      </div>

      {/* Marketplace */}
      <div className="panel">
        <div className="panel-title"><IconChip size={16} /> Marketplace</div>
        <p className="ais-intro">
          A curated registry of community plugins. Every file is reviewed and{" "}
          <b>checksum-verified at install time</b>, installs one click, and everything runs
          locally. Plugins run with TailCam's full privileges — only install what you trust.
        </p>
        {data?.error && (
          <div className="plug-errors"><div className="plug-err">⚠ {data.error}</div></div>
        )}
        {market.isLoading ? (
          <div className="empty"><Spinner size={20} /></div>
        ) : (data?.market.length ?? 0) === 0 && !data?.error ? (
          <p className="ais-intro">The registry is empty right now — check back soon.</p>
        ) : (
          <div className="plug-market-grid">
            {data?.market.map((p) => (
              <div key={p.id} className="plug-card">
                <div className="plug-card-head">
                  <div className="plug-name">{p.name}</div>
                  <span className="mono plug-ver">v{p.version}</span>
                </div>
                <div className="plug-kinds">{p.kinds.map(kindChip)}</div>
                <p className="plug-desc">{p.description}</p>
                {p.author && <div className="plug-author mono">by {p.author}</div>}
                {p.settings_example && (
                  <details className="plug-settings">
                    <summary>Settings (config.toml)</summary>
                    <pre className="mono">{p.settings_example}</pre>
                  </details>
                )}
                <div className="plug-card-actions">
                  {p.installed && !p.update_available ? (
                    <span className="ais-badge active">✓ installed{p.installed_version ? ` v${p.installed_version}` : ""}</span>
                  ) : (
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => install(p)}
                      disabled={actions.install.isPending}
                    >
                      {p.update_available ? `Update to v${p.version}` : "Install"}
                    </Button>
                  )}
                  {p.homepage && (
                    <a className="plug-link mono" href={p.homepage} target="_blank" rel="noreferrer">
                      source ↗
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="plug-registry mono">registry: {data?.registry_url ?? "…"}</div>
      </div>

      {/* Capabilities the loaded plugins contribute right now */}
      <CapabilitiesSummary />

      {/* Build your own */}
      <div className="panel ais-train">
        <div className="ais-train-ic"><IconBook size={20} /></div>
        <div className="ais-train-text">
          <b>Build your own plugin</b>
          <span>
            One Python file: implement a hook, drop it in, done. The authoring guide covers AI
            providers, notification channels, event hooks, settings, and how to submit yours to
            the marketplace for everyone.
          </span>
        </div>
        <Button variant="ghost" onClick={() => (window.location.href = "/docs/plugins")}>
          Authoring guide →
        </Button>
      </div>

      <ConfirmDialog
        open={confirmRemove !== null}
        title={`Uninstall ${confirmRemove?.id}?`}
        body="The plugin file is deleted from this node and its capabilities unload immediately. Its settings in config.toml are kept."
        confirmLabel="Uninstall"
        onConfirm={() => {
          const p = confirmRemove!;
          setConfirmRemove(null);
          void run(() => actions.uninstall.mutateAsync(p.id), `${p.id} uninstalled`);
        }}
        onCancel={() => setConfirmRemove(null)}
      />
    </div>
  );
}

function CapabilitiesSummary() {
  const data = usePlugins().data;
  if (!data) return null;
  return <PluginsPanel />;
}
