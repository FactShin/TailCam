import { usePlugins } from "../api/hooks";
import { IconAi, IconBell, IconChip } from "../icons";

function kindIcon(kind: string) {
  if (kind === "ai") return <IconAi size={16} />;
  if (kind === "notification") return <IconBell size={16} />;
  return <IconChip size={16} />;
}

export function PluginsPanel() {
  const data = usePlugins().data;
  const plugins = data?.plugins ?? [];

  return (
    <div className="panel notif-panel">
      <div className="panel-title"><IconChip size={16} /> Plugins</div>
      <p className="ais-intro">
        Plugins extend TailCam with extra AI providers and notification channels. Add one by
        <span className="mono lit"> pip install</span>-ing a plugin package, or by dropping a
        single <span className="mono lit">.py</span> file into your config folder's
        <span className="mono lit"> plugins/</span> directory — then restart.
      </p>

      <div className="plug-list">
        {plugins.map((p) => (
          <div key={p.id} className="plug-row">
            <span className="plug-ic">{kindIcon(p.kind)}</span>
            <div className="plug-main">
              <div className="plug-name">{p.name}</div>
              {p.description && <div className="plug-desc">{p.description}</div>}
            </div>
            <div className="plug-badges">
              <span className="ais-badge ok">{p.kind}</span>
              <span className={`ais-badge ${p.builtin ? "rec" : "active"}`}>
                {p.builtin ? "built-in" : "external"}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="plug-summary">
        <span><b>AI providers:</b> {(data?.analyzer_providers ?? []).map((x) => x.id).join(", ") || "—"}</span>
        <span><b>Channels:</b> {(data?.notification_channels ?? []).map((x) => x.id).join(", ") || "—"}</span>
        <span><b>Active AI:</b> {data?.active_provider ?? "—"}</span>
      </div>

      {(data?.errors?.length ?? 0) > 0 && (
        <div className="plug-errors">
          {data!.errors.map((e, i) => <div key={i} className="plug-err">⚠ {e}</div>)}
        </div>
      )}
    </div>
  );
}
