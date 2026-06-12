import { IconMotion } from "../icons";
import type { CameraInfo, ViewParams } from "../types";
import { LiveViewer } from "./LiveViewer";
import { StatusPill } from "./ui";

const LOW_BW: ViewParams = { fps: 8, zoom: 1, panX: 0.5, panY: 0.5, quality: 55, w: 480 };

export function CameraTile({ cam, onOpen }: { cam: CameraInfo; onOpen: () => void }) {
  return (
    <button className="tile tile-cinematic" onClick={onOpen} aria-label={`Open ${cam.name}`}>
      <div className="tile-media">
        <LiveViewer cam={cam} view={LOW_BW} showOsd={false} />
        <div className="tile-grad" />
        <div className="tile-badges">
          {cam.motion_enabled && (
            <span className="tile-motion" title="Motion armed"><IconMotion size={15} /></span>
          )}
          {cam.recording && <span className="tile-rec" title="Recording"><span className="rec-dot" /></span>}
        </div>
        <div className="tile-overlay">
          <div className="tile-over-top">
            <StatusPill status={cam.status} fps={cam.fps} size="sm" />
          </div>
          <div className="tile-over-bottom">
            <span className="tile-name">{cam.name}</span>
            <span className="tile-id mono">{cam.id}</span>
          </div>
        </div>
      </div>
    </button>
  );
}
