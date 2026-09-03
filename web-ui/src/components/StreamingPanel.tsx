import { useEffect, useState } from "react";

import { useStreaming, useUpdateStreaming } from "../api/hooks";
import { IconFps } from "../icons";
import { useToast } from "./toast";
import { Button, Segmented } from "./ui";

/** Global stream defaults: every camera without its own override streams at
 * these (fps, JPEG quality, max width). One setting for the whole system —
 * per-camera overrides live on each camera's page. */
export function StreamingPanel() {
  const data = useStreaming().data;
  const update = useUpdateStreaming();
  const toast = useToast();
  const [fps, setFps] = useState(15);
  const [quality, setQuality] = useState(80);
  const [maxWidth, setMaxWidth] = useState(1280);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!data || dirty) return;
    setFps(data.fps);
    setQuality(data.quality);
    setMaxWidth(data.max_width);
  }, [data, dirty]);

  if (!data) return null;

  const save = async () => {
    try {
      await update.mutateAsync({ fps, quality, max_width: maxWidth });
      setDirty(false);
      toast.ok("Streaming defaults saved for all cameras");
    } catch {
      toast.err("Could not save");
    }
  };

  return (
    <div className="panel notif-panel">
      <div className="panel-title"><IconFps size={16} /> Streaming (all cameras)</div>
      <p className="ais-intro">
        The frame rate, JPEG quality, and size every camera streams at, on every device and
        browser. A camera can override these on its own page. Lower values cost less CPU and
        bandwidth on the node that owns the camera — on a Raspberry Pi, 10 fps at 960 px wide
        is a good balance.
      </p>
      <div className="notif-grid">
        <label className="tl-field">
          <span className="microlabel">Frame rate (fps)</span>
          <input className="tl-input" type="number" min={1} max={60} value={fps}
            onChange={(e) => { setFps(Math.min(60, Math.max(1, Math.round(Number(e.target.value) || 1)))); setDirty(true); }} />
        </label>
        <label className="tl-field">
          <span className="microlabel">JPEG quality (%)</span>
          <input className="tl-input" type="number" min={1} max={100} value={quality}
            onChange={(e) => { setQuality(Math.min(100, Math.max(1, Math.round(Number(e.target.value) || 1)))); setDirty(true); }} />
        </label>
      </div>
      <div className="notif-row">
        <span className="microlabel">Max width</span>
        <Segmented ariaLabel="Max stream width" value={maxWidth}
          options={[{ value: 0, label: "Native" }, { value: 640, label: "640" }, { value: 960, label: "960" }, { value: 1280, label: "1280" }, { value: 1920, label: "1920" }]}
          onChange={(v) => { setMaxWidth(v as number); setDirty(true); }} />
      </div>
      <div className="notif-actions">
        <Button variant="primary" disabled={!dirty || update.isPending} onClick={save}>
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
