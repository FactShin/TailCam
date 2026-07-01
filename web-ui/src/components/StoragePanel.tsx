import { useEffect, useState } from "react";

import { useStorage, useUpdateStorage } from "../api/hooks";
import { IconHdd } from "../icons";
import { fmtBytes } from "../lib/format";
import { useToast } from "./toast";
import { Button, Toggle } from "./ui";

export function StoragePanel() {
  const data = useStorage().data;
  const update = useUpdateStorage();
  const toast = useToast();

  // The two forms (save location vs recording/retention) are independent —
  // each has its own dirty flag so saving one never clobbers unsaved edits in
  // the other.
  const [dir, setDir] = useState("");
  const [dirDirty, setDirDirty] = useState(false);
  const [tail, setTail] = useState(5);
  const [maxGb, setMaxGb] = useState(10);
  const [maxAge, setMaxAge] = useState(30);
  const [retDirty, setRetDirty] = useState(false);

  useEffect(() => {
    if (!data) return;
    if (!dirDirty) setDir(data.custom_dir);
    if (!retDirty) {
      setTail(data.record_tail_seconds);
      setMaxGb(data.max_gb);
      setMaxAge(data.max_age_days);
    }
  }, [data, dirDirty, retDirty]);

  if (!data) return null;

  const setAutoRecord = (v: boolean) => update.mutate({ auto_record: v });
  const setRetentionEnabled = (v: boolean) => update.mutate({ retention_enabled: v });

  const saveLocation = async () => {
    try {
      await update.mutateAsync({ media_dir: dir });
      setDirDirty(false);
      toast.ok(dir.trim() ? "Save location updated" : "Reverted to default location");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not set location");
    }
  };
  const resetLocation = async () => {
    setDir("");
    setDirDirty(false);
    try {
      await update.mutateAsync({ media_dir: "" });
      toast.ok("Reverted to default location");
    } catch {
      toast.err("Could not reset");
    }
  };
  const saveRetention = async () => {
    try {
      await update.mutateAsync({
        record_tail_seconds: tail,
        max_gb: maxGb,
        max_age_days: maxAge,
      });
      setRetDirty(false);
      toast.ok("Recording settings saved");
    } catch {
      toast.err("Could not save");
    }
  };

  const usedPct =
    data.disk_total > 0 ? Math.min(100, Math.round((data.disk_used / data.disk_total) * 100)) : 0;

  return (
    <div className="panel notif-panel">
      <div className="panel-title">
        <IconHdd size={16} /> Recording &amp; storage
      </div>
      <p className="ais-intro">
        Where recordings and snapshots are saved on disk, whether motion events save a clip, and how
        much history to keep.
      </p>

      {/* save location */}
      <div className="notif-row">
        <span className="microlabel">Save location</span>
        <div className="stor-path">
          <span className="mono">{data.media_dir}</span>
          <span className={`ais-badge ${data.is_default ? "rec" : "active"}`}>
            {data.is_default ? "default" : "custom"}
          </span>
          {!data.writable && <span className="ais-badge warn">not writable</span>}
        </div>
      </div>
      <div className="notif-grid">
        <label className="tl-field">
          <span className="microlabel">Custom folder (blank = default · point at a drive/NAS)</span>
          <input
            className="tl-input"
            value={dir}
            placeholder="/mnt/usb/tailcam or D:\\TailCam"
            onChange={(e) => {
              setDir(e.target.value);
              setDirDirty(true);
            }}
          />
        </label>
      </div>
      <div className="notif-actions">
        <Button variant="primary" disabled={update.isPending} onClick={saveLocation}>
          {update.isPending ? "Saving…" : "Set location"}
        </Button>
        {!data.is_default && (
          <Button variant="ghost" disabled={update.isPending} onClick={resetLocation}>
            Reset to default
          </Button>
        )}
      </div>

      {/* disk usage */}
      <div className="stor-disk">
        <div className="stor-bar">
          <i style={{ width: `${usedPct}%` }} />
        </div>
        <div className="stor-stats mono">
          <span>{fmtBytes(data.media_bytes)} in recordings + snapshots · {data.media_count} files</span>
          <span>{fmtBytes(data.disk_free)} free of {fmtBytes(data.disk_total)}</span>
        </div>
      </div>

      {/* record on motion */}
      <div className="notif-row">
        <span className="microlabel">Record on motion</span>
        <div className="notif-trigs">
          <span className="notif-trig">
            <Toggle
              checked={data.auto_record}
              label="Save a clip when motion is detected"
              onChange={setAutoRecord}
            />
            <span>Save a clip when motion is detected</span>
          </span>
        </div>
      </div>
      <p className="stor-note">
        Also turn on <b>Motion detection</b> for each camera (on its page) — events then save a video
        here automatically.
      </p>

      {/* tail */}
      <div className="notif-grid">
        <label className="tl-field">
          <span className="microlabel">Keep recording after motion ends (seconds)</span>
          <input className="tl-input" type="number" min={0} value={tail}
            onChange={(e) => { setTail(parseFloat(e.target.value) || 0); setRetDirty(true); }} />
        </label>
      </div>

      {/* auto-cleanup (opt-in retention) */}
      <div className="notif-row">
        <span className="microlabel">Auto-cleanup</span>
        <div className="notif-trigs">
          <span className="notif-trig">
            <Toggle
              checked={data.retention_enabled}
              label="Automatically delete old media to stay under the limits"
              onChange={setRetentionEnabled}
            />
            <span>Automatically delete old media to stay under the limits</span>
          </span>
        </div>
      </div>
      {data.retention_enabled && (
        <div className="notif-grid">
          <label className="tl-field">
            <span className="microlabel">Storage budget (GB)</span>
            <input className="tl-input" type="number" min={0.1} step={0.5} value={maxGb}
              onChange={(e) => { setMaxGb(parseFloat(e.target.value) || 0.1); setRetDirty(true); }} />
          </label>
          <label className="tl-field">
            <span className="microlabel">Delete media older than (days)</span>
            <input className="tl-input" type="number" min={1} value={maxAge}
              onChange={(e) => { setMaxAge(parseInt(e.target.value) || 1); setRetDirty(true); }} />
          </label>
        </div>
      )}
      <div className="notif-actions">
        <Button variant="primary" disabled={!retDirty || update.isPending} onClick={saveRetention}>
          {update.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
