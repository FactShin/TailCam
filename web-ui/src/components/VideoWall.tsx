import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useCameras } from "../api/hooks";
import { IconClose } from "../icons";
import { cameraPath } from "../lib/nav";
import type { CameraInfo, ViewParams } from "../types";
import { LiveViewer } from "./LiveViewer";

// Wall tiles use a low-bandwidth stream; the focused feed gets full quality.
const WALL_VIEW: ViewParams = { fps: 8, zoom: 1, panX: 0.5, panY: 0.5, quality: 55, w: 640 };
const FOCUS_VIEW: ViewParams = { fps: 15, zoom: 1, panX: 0.5, panY: 0.5, quality: 75, w: 0 };

export function VideoWall({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const cams = (useCameras().data ?? []).filter((c) => c.status !== "offline");
  const [focus, setFocus] = useState<number | null>(null);
  const [chromeHidden, setChromeHidden] = useState(false);

  // ESC exits focus, then the wall; arrows switch the focused feed.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        if (focus != null) setFocus(null);
        else onClose();
      }
      if (focus != null && cams.length > 0) {
        if (e.key === "ArrowRight") setFocus((f) => ((f ?? 0) + 1) % cams.length);
        if (e.key === "ArrowLeft") setFocus((f) => ((f ?? 0) - 1 + cams.length) % cams.length);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [focus, cams.length, onClose]);

  // Hide the header chrome after a few seconds of mouse idle.
  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    const poke = () => {
      setChromeHidden(false);
      clearTimeout(t);
      t = setTimeout(() => setChromeHidden(true), 3000);
    };
    poke();
    window.addEventListener("mousemove", poke);
    return () => { clearTimeout(t); window.removeEventListener("mousemove", poke); };
  }, []);

  const openCamera = (cam: CameraInfo) => {
    onClose();
    navigate(cameraPath(cam));
  };

  const cols = cams.length <= 1 ? 1 : cams.length <= 4 ? 2 : 3;

  return (
    <div className={`wall ${chromeHidden ? "chrome-hidden" : ""}`} role="dialog" aria-label="Video wall">
      <div className="wall-head">
        <span className="microlabel lit">Video Wall</span>
        <span className="microlabel">{cams.length} feeds</span>
        <span style={{ flex: 1 }} />
        <span className="microlabel">{focus != null ? "← → switch · ESC grid" : "click feed to focus · ESC exit"}</span>
        <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close video wall">
          <IconClose size={15} />
        </button>
      </div>

      {cams.length === 0 ? (
        <div className="empty"><div className="empty-title">No online cameras</div></div>
      ) : focus == null ? (
        <div className="wall-grid" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
          {cams.map((c, i) => (
            <div key={`${c.host}/${c.id}`} className="wall-cell" onClick={() => setFocus(i)}>
              <LiveViewer cam={c} view={WALL_VIEW} showOsd={false} />
              <span className="wall-osd">
                <span className={`led ${c.status === "online" ? "ok" : "warn"}`} />
                {c.name.toUpperCase()}
                {c.recording && <span style={{ color: "var(--err)" }}>● REC</span>}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="wall-focus">
          <div className="wall-focus-main" onDoubleClick={() => openCamera(cams[focus])}>
            <LiveViewer cam={cams[focus]} view={FOCUS_VIEW} fit="contain" big />
            <span className="wall-osd" style={{ fontSize: 12 }}>
              <span className={`led ${cams[focus].status === "online" ? "ok" : "warn"}`} />
              {cams[focus].name.toUpperCase()} — double-click to open controls
            </span>
          </div>
          <div className="wall-rail">
            {cams.map((c, i) => (
              <div key={`${c.host}/${c.id}`} className={`wall-cell ${i === focus ? "is-focus" : ""}`} onClick={() => setFocus(i)}>
                <LiveViewer cam={c} view={WALL_VIEW} showOsd={false} />
                <span className="wall-osd">{c.name.toUpperCase()}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
