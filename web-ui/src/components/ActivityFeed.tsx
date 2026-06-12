import { useNavigate } from "react-router-dom";

import { eventThumbUrl } from "../api/client";
import { useCameras, useEvents } from "../api/hooks";
import { fmtAgo } from "../lib/format";
import { cameraPath } from "../lib/nav";

/** Live activity sidebar: most recent motion events across all nodes.
    Shown on the dashboard at wide container sizes (≥1280px). */
export function ActivityFeed() {
  const navigate = useNavigate();
  const cameras = useCameras().data ?? [];
  const events = useEvents({ limit: 14 }).data ?? [];
  const camName = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;

  return (
    <aside className="actfeed" aria-label="Live activity">
      <div className="actfeed-head">
        <span className="led ok blink" />
        <span className="microlabel lit">Live Activity</span>
        <span className="grow" />
        <span className="microlabel">{events.length}</span>
      </div>
      <div className="actfeed-list">
        {events.length === 0 && (
          <div style={{ padding: "18px 14px" }} className="microlabel">No motion events yet</div>
        )}
        {events.map((e) => {
          const ongoing = e.end_ts == null;
          return (
            <button
              key={`${e.host}/${e.id}`}
              className={`actrow ${ongoing ? "is-live" : ""}`}
              onClick={() => navigate(cameraPath({ host: e.host, id: e.camera_id }))}
            >
              <span className="actrow-thumb">
                {e.has_thumb ? (
                  <img src={eventThumbUrl(e.proxy_prefix, e.id)} alt="" loading="lazy" />
                ) : (
                  <span className={`led ${ongoing ? "err" : "off"}`} />
                )}
              </span>
              <span className="actrow-body">
                <span className="actrow-l1">
                  {camName(e.camera_id)}
                  {e.label && <span className={`event-label label-${e.label}`}>{e.label}</span>}
                  {ongoing && <span className="evlive"><span className="rec-dot" />LIVE</span>}
                </span>
                <span className="actrow-l2">
                  {fmtAgo(e.start_ts)} · peak {Math.round(e.peak_score * 100)}%
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
