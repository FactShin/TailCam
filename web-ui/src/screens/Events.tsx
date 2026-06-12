import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { eventThumbUrl } from "../api/client";
import { useCameras, useEvents } from "../api/hooks";
import { Button } from "../components/ui";
import { IconMotion, IconPlay } from "../icons";
import { fmtDateTime, fmtDur } from "../lib/format";
import type { CameraInfo } from "../types";

const LABEL_ICON: Record<string, string> = {
  person: "🧍",
  animal: "🐾",
  vehicle: "🚗",
  package: "📦",
  plant: "🌿",
  nothing: "—",
};

function CamFilter({
  cameras,
  value,
  onChange,
}: {
  cameras: CameraInfo[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="filter-scroll">
      <button className={`chip-filter ${!value ? "is-on" : ""}`} onClick={() => onChange("")}>All cameras</button>
      {cameras.map((c) => (
        <button key={`${c.host}/${c.id}`} className={`chip-filter ${value === c.id ? "is-on" : ""}`} onClick={() => onChange(c.id)}>
          {c.name}
        </button>
      ))}
    </div>
  );
}

export function Events() {
  const navigate = useNavigate();
  const cameras = useCameras().data ?? [];
  const camName = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;
  const [cam, setCam] = useState("");
  const events = useEvents(cam ? { camera_id: cam, limit: 80 } : { limit: 80 }).data ?? [];

  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <h1 className="screen-title">Motion events</h1>
          <p className="screen-sub">{events.length} event{events.length !== 1 ? "s" : ""} · newest first · all devices</p>
        </div>
      </div>
      <CamFilter cameras={cameras} value={cam} onChange={setCam} />

      {events.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconMotion size={36} /></div>
          <div className="empty-title">No motion events</div>
          <div className="empty-sub">Events appear when motion-enabled cameras detect movement.</div>
        </div>
      ) : (
        <div className="event-list">
          {events.map((e) => {
            const ongoing = e.end_ts == null;
            const dur = ongoing ? now - e.start_ts : e.end_ts! - e.start_ts;
            const pct = Math.round(e.peak_score * 100);
            const sev = pct >= 75 ? "high" : pct >= 50 ? "mid" : "low";
            const icon = LABEL_ICON[e.label ?? ""] ?? "•";
            return (
              <div key={`${e.host}/${e.id}`} className={`event-row ${ongoing ? "is-live" : ""}`}>
                {e.has_thumb ? (
                  <img className="event-thumb" src={eventThumbUrl(e.proxy_prefix, e.id)} alt="" loading="lazy" />
                ) : (
                  <div className="event-score">
                    <div className={`score-ring score-${sev}`} style={{ ["--p" as string]: pct }}>
                      <span className="mono">{pct}%</span>
                    </div>
                  </div>
                )}
                <div className="event-body">
                  <div className="event-l1">
                    <span className="event-cam">{camName(e.camera_id)}</span>
                    {e.label && <span className={`event-label label-${e.label}`}>{icon} {e.label}</span>}
                    {ongoing && <span className="event-ongoing"><span className="rec-dot" /> ongoing</span>}
                  </div>
                  <div className="event-l2 mono">
                    {e.host} · {fmtDateTime(e.start_ts)} · {fmtDur(dur)} · peak {pct}%
                  </div>
                  {e.description && <div className="event-desc">{e.description}</div>}
                </div>
                <div className="event-actions">
                  {e.recording_id != null ? (
                    <Button variant="outline" size="sm" icon={<IconPlay size={14} />} onClick={() => navigate("/gallery")}>View clip</Button>
                  ) : (
                    <span className="event-noclip mono">no clip</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
