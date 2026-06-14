import { useEffect, useState } from "react";

import { timelapseFileUrl, timelapseThumbUrl } from "../api/client";
import {
  useCameras,
  useDeleteTimelapse,
  useEncodeTimelapse,
  useStartTimelapse,
  useStopTimelapse,
  useTimelapses,
} from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, Spinner } from "../components/ui";
import {
  IconBolt,
  IconClose,
  IconDownload,
  IconPlay,
  IconStop,
  IconTimelapse,
  IconTrash,
} from "../icons";
import { fmtBytes, fmtDateTime, fmtDur } from "../lib/format";
import type { TimelapseInfo, TimelapseState } from "../types";

const STATE_BADGE: Record<TimelapseState, { cls: string; label: string }> = {
  capturing: { cls: "badge-ok", label: "Capturing" },
  encoding: { cls: "badge-warn", label: "Encoding" },
  complete: { cls: "badge-ok", label: "Ready" },
  interrupted: { cls: "badge-warn", label: "Interrupted" },
  error: { cls: "badge-err", label: "Failed" },
};

// frames at output_fps → seconds of finished video
const videoSeconds = (t: TimelapseInfo) => t.frames_captured / Math.max(1, t.output_fps);

export function Timelapse() {
  const toast = useToast();
  const cameras = useCameras().data ?? [];
  const rows = useTimelapses().data ?? [];
  const start = useStartTimelapse();
  const stop = useStopTimelapse();
  const encode = useEncodeTimelapse();
  const del = useDeleteTimelapse();

  const [camId, setCamId] = useState("");
  const [interval, setIntervalSec] = useState(2);
  const [fps, setFps] = useState(30);
  const [name, setName] = useState("");
  const [play, setPlay] = useState<TimelapseInfo | null>(null);
  const [confirm, setConfirm] = useState<TimelapseInfo | null>(null);

  // tick so live "capturing" durations advance each second between refetches
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!camId && cameras.length) setCamId(cameras[0].id);
  }, [cameras, camId]);

  const camName = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;
  const selected = cameras.find((c) => c.id === camId);

  const active = rows.filter((r) => r.state === "capturing" || r.state === "encoding");
  const done = rows.filter((r) => r.state !== "capturing" && r.state !== "encoding");

  const onStart = async () => {
    if (!selected) return;
    try {
      await start.mutateAsync({
        prefix: selected.proxy_prefix,
        id: selected.id,
        params: { interval_seconds: interval, output_fps: fps, name: name.trim() || undefined },
      });
      toast.ok("Timelapse started");
      setName("");
    } catch {
      toast.err("Could not start timelapse");
    }
  };

  const onStop = async (t: TimelapseInfo) => {
    try {
      await stop.mutateAsync({ prefix: t.proxy_prefix, id: t.id });
      toast.ok("Stopping — encoding video…");
    } catch {
      toast.err("Could not stop");
    }
  };

  const onEncode = async (t: TimelapseInfo) => {
    try {
      await encode.mutateAsync({ prefix: t.proxy_prefix, id: t.id });
      toast.ok("Encoding…");
    } catch {
      toast.err("Could not encode");
    }
  };

  const onDelete = async (t: TimelapseInfo) => {
    setConfirm(null);
    try {
      await del.mutateAsync({ prefix: t.proxy_prefix, id: t.id });
      if (play?.id === t.id) setPlay(null);
      toast.ok("Deleted");
    } catch {
      toast.err("Delete failed");
    }
  };

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <div className="kicker"><span className="kicker-rule" /><span className="microlabel lit">3D Print &amp; Timelapse</span></div>
          <h1 className="screen-title">Timelapse</h1>
          <p className="screen-sub">{rows.length} capture{rows.length !== 1 ? "s" : ""} · this device</p>
        </div>
      </div>

      {/* New capture */}
      <div className="panel tl-new">
        <div className="panel-title"><IconTimelapse size={16} /> New capture</div>
        <div className="tl-form-grid">
          <label className="tl-field">
            <span className="microlabel">Camera</span>
            <select className="tl-select" value={camId} onChange={(e) => setCamId(e.target.value)}>
              {cameras.length === 0 && <option value="">No cameras</option>}
              {cameras.map((c) => (
                <option key={`${c.host}/${c.id}`} value={c.id}>
                  {c.name} · {c.host}
                </option>
              ))}
            </select>
          </label>
          <label className="tl-field">
            <span className="microlabel">Frame interval (s)</span>
            <input
              className="tl-input"
              type="number"
              min={0.2}
              step={0.5}
              value={interval}
              onChange={(e) => setIntervalSec(Math.max(0.2, Number(e.target.value) || 0.2))}
            />
          </label>
          <label className="tl-field">
            <span className="microlabel">Playback FPS</span>
            <input
              className="tl-input"
              type="number"
              min={1}
              max={60}
              step={1}
              value={fps}
              onChange={(e) => setFps(Math.min(60, Math.max(1, Math.round(Number(e.target.value) || 1))))}
            />
          </label>
          <label className="tl-field tl-field-wide">
            <span className="microlabel">Name (optional)</span>
            <input
              className="tl-input"
              type="text"
              placeholder={selected ? `${camName(selected.id)} timelapse` : "Timelapse"}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="tl-field tl-field-action">
            <Button
              variant="primary"
              icon={<IconBolt size={15} />}
              disabled={!selected || start.isPending}
              onClick={onStart}
            >
              Start capture
            </Button>
          </div>
        </div>
        <p className="help-foot mono">
          Tip for 3D prints: a short interval (1–2s) captures the print growing. Raw frames are
          kept, so a future update can smooth the motion with interpolation.
        </p>
      </div>

      {/* Active captures */}
      {active.length > 0 && (
        <div className="tl-active">
          {active.map((t) => {
            const b = STATE_BADGE[t.state];
            const elapsed = (t.end_ts ?? Date.now() / 1000) - t.start_ts;
            return (
              <div key={t.id} className={`tl-active-row ${t.state === "capturing" ? "is-rec" : ""}`}>
                <span className={`badge ${b.cls}`}>
                  <span className="pill-dot" style={{ background: t.state === "capturing" ? "var(--ok)" : "var(--warn)" }} />
                  {b.label}
                </span>
                <div className="tl-grow">
                  <div className="tl-name">{t.name}</div>
                  <div className="tl-meta mono">
                    {camName(t.camera_id)} · {t.frames_captured} frames · {fmtDur(elapsed)} · every {t.interval_seconds}s
                  </div>
                </div>
                {t.state === "capturing" ? (
                  <Button variant="danger" size="sm" icon={<IconStop size={14} />} onClick={() => onStop(t)}>
                    Stop
                  </Button>
                ) : (
                  <span className="tl-encoding mono"><Spinner size={14} /> encoding…</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Finished / interrupted */}
      {done.length === 0 && active.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconTimelapse size={36} /></div>
          <div className="empty-title">No timelapses yet</div>
          <div className="empty-sub">Start a capture above — great for watching a 3D print come together.</div>
        </div>
      ) : (
        <div className="tl-grid">
          {done.map((t) => {
            const b = STATE_BADGE[t.state];
            const playable = t.state === "complete" && t.has_video;
            return (
              <div key={t.id} className="tl-card">
                <div
                  className="media-thumb tl-thumb"
                  onClick={() => playable && setPlay(t)}
                  role={playable ? "button" : undefined}
                  aria-label={playable ? `Play ${t.name}` : t.name}
                >
                  {t.has_thumb ? (
                    <img className="thumb-canvas" src={timelapseThumbUrl(t.proxy_prefix, t.id)} alt="" loading="lazy" />
                  ) : (
                    <div className="tl-thumb-empty"><IconTimelapse size={28} /></div>
                  )}
                  {playable && <span className="media-play"><IconPlay size={20} /></span>}
                  <span className={`badge tl-thumb-badge ${b.cls}`}>{b.label}</span>
                </div>
                <div className="tl-body">
                  <span className="tl-name">{t.name}</span>
                  <span className="tl-meta mono">{camName(t.camera_id)} · {fmtDateTime(t.created_ts)}</span>
                  <span className="tl-meta mono">
                    {t.frames_captured} frames
                    {t.state === "complete" && ` · ${videoSeconds(t).toFixed(1)}s @ ${t.output_fps}fps · ${fmtBytes(t.size_bytes)}`}
                  </span>
                </div>
                <div className="tl-foot">
                  {playable && (
                    <a className="btn btn-outline btn-sm" href={timelapseFileUrl(t.proxy_prefix, t.id)} download>
                      <span className="btn-ic"><IconDownload size={14} /></span><span>Save</span>
                    </a>
                  )}
                  {(t.state === "interrupted" || t.state === "error") && (
                    <Button variant="outline" size="sm" icon={<IconBolt size={14} />} onClick={() => onEncode(t)}>
                      Encode
                    </Button>
                  )}
                  <Button variant="danger" size="sm" icon={<IconTrash size={14} />} onClick={() => setConfirm(t)}>
                    Delete
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {play && (
        <div className="lb-root" role="dialog" aria-modal="true" aria-label="Timelapse player">
          <div className="lb-backdrop" onClick={() => setPlay(null)} />
          <button className="lb-x" onClick={() => setPlay(null)} aria-label="Close"><IconClose size={20} /></button>
          <div className="lb-stage">
            <div className="lb-media">
              <video
                className="lb-canvas"
                src={timelapseFileUrl(play.proxy_prefix, play.id)}
                poster={play.has_thumb ? timelapseThumbUrl(play.proxy_prefix, play.id) : undefined}
                controls
                autoPlay
                loop
                playsInline
              />
            </div>
            <div className="lb-info">
              <div className="lb-info-l">
                <span className="lb-cam">{play.name}</span>
                <span className="lb-sub mono">
                  {camName(play.camera_id)} · {play.frames_captured} frames · {videoSeconds(play).toFixed(1)}s @ {play.output_fps}fps · {fmtBytes(play.size_bytes)}
                </span>
              </div>
              <div className="lb-actions">
                <a className="btn btn-outline btn-sm" href={timelapseFileUrl(play.proxy_prefix, play.id)} download>
                  <span className="btn-ic"><IconDownload size={15} /></span><span>Download</span>
                </a>
                <Button variant="danger" size="sm" icon={<IconTrash size={15} />} onClick={() => setConfirm(play)}>Delete</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!confirm}
        title="Delete timelapse?"
        danger
        confirmLabel="Delete"
        body={confirm ? `“${confirm.name}” and its captured frames will be permanently removed.` : ""}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && onDelete(confirm)}
      />
    </div>
  );
}
