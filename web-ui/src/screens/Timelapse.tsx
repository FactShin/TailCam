import { useEffect, useState } from "react";

import {
  timelapseFileUrl,
  timelapseFrameUrl,
  timelapseSmoothUrl,
  timelapseThumbUrl,
} from "../api/client";
import {
  useAi,
  useCameras,
  useDeleteTimelapse,
  useEncodeTimelapse,
  usePostprocess,
  useSmoothTimelapse,
  useStartTimelapse,
  useStopTimelapse,
  useTimelapseAnalysisEvents,
  useTimelapsePresets,
  useTimelapses,
} from "../api/hooks";
import { LiveViewer } from "../components/LiveViewer";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, Segmented, Spinner } from "../components/ui";
import {
  IconBolt,
  IconClose,
  IconDownload,
  IconPlay,
  IconSparkle,
  IconStop,
  IconTimelapse,
  IconTrash,
} from "../icons";
import { fmtBytes, fmtDateTime, fmtDur } from "../lib/format";
import type { TimelapseInfo, TimelapseStartParams, TimelapseState, ViewParams } from "../types";

const STATE_BADGE: Record<TimelapseState, { cls: string; label: string }> = {
  capturing: { cls: "badge-ok", label: "Capturing" },
  encoding: { cls: "badge-warn", label: "Encoding" },
  complete: { cls: "badge-ok", label: "Ready" },
  interrupted: { cls: "badge-warn", label: "Interrupted" },
  error: { cls: "badge-err", label: "Failed" },
};

// Live preview while capturing — a modest stream (the capture itself is the
// real artifact; this is just to watch it happen and stop it).
const PREVIEW_VIEW: ViewParams = { fps: 12, zoom: 1, panX: 0.5, panY: 0.5, quality: 70, w: 960 };

// frames at output_fps → seconds of finished video
const videoSeconds = (t: TimelapseInfo) => t.frames_captured / Math.max(1, t.output_fps);

type PrinterSettings = Required<Omit<TimelapseStartParams, "name">>;

const DEFAULT_SETTINGS: PrinterSettings = {
  interval_seconds: 2,
  output_fps: 30,
  duration_seconds: 0,
  jpeg_quality: 95,
  max_frames: 0,
  auto_smooth: true,
  smooth_target_fps: 60,
  smooth_interpolate: true,
  smooth_deflicker: true,
  smooth_engine: "ffmpeg",
  smooth_quality: "high",
  analysis_enabled: false,
  analysis_cadence_seconds: 60,
};

const HEALTH_BADGE: Record<string, { cls: string; label: string }> = {
  healthy: { cls: "badge-ok", label: "Print healthy" },
  possible_failure: { cls: "badge-warn", label: "Possible failure" },
  failure: { cls: "badge-err", label: "Failure detected" },
  uncertain: { cls: "badge-warn", label: "Analysis uncertain" },
};

export function Timelapse() {
  const toast = useToast();
  const cameras = useCameras().data ?? [];
  const rows = useTimelapses().data ?? [];
  const presets = useTimelapsePresets().data ?? [];
  const ai = useAi().data;
  const start = useStartTimelapse();
  const stop = useStopTimelapse();
  const encode = useEncodeTimelapse();
  const smooth = useSmoothTimelapse();
  const del = useDeleteTimelapse();
  const postprocess = usePostprocess().data;

  const [camId, setCamId] = useState("");
  const [name, setName] = useState("");
  const [presetName, setPresetName] = useState("Reliable Print");
  const [settings, setSettings] = useState<PrinterSettings>(DEFAULT_SETTINGS);
  const [advanced, setAdvanced] = useState(false);
  const [play, setPlay] = useState<TimelapseInfo | null>(null);
  const [playSmooth, setPlaySmooth] = useState(true);
  const [confirm, setConfirm] = useState<TimelapseInfo | null>(null);
  const analysisEvents = useTimelapseAnalysisEvents(
    play?.proxy_prefix ?? "",
    play?.id ?? null,
  ).data ?? [];

  // Default the player to the smoothed cut when one exists.
  useEffect(() => {
    if (play) setPlaySmooth(play.has_smooth);
  }, [play]);

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

  const applyPreset = (nextName: string) => {
    setPresetName(nextName);
    const preset = presets.find((item) => item.name === nextName);
    if (preset) setSettings((current) => ({ ...current, ...preset.settings }));
  };

  const onStart = async () => {
    if (!selected) return;
    try {
      await start.mutateAsync({
        prefix: selected.proxy_prefix,
        id: selected.id,
        params: { ...settings, name: name.trim() || undefined },
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

  const onSmooth = async (t: TimelapseInfo) => {
    try {
      await smooth.mutateAsync({
        prefix: t.proxy_prefix,
        id: t.id,
        params: {
          target_fps: settings.smooth_target_fps,
          interpolate: settings.smooth_interpolate,
          deflicker: settings.smooth_deflicker,
          engine: settings.smooth_engine,
          quality: settings.smooth_quality,
        },
      });
      toast.ok("Smoothing — interpolating frames…");
    } catch {
      toast.err("Could not start smoothing");
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
            <span className="microlabel">Printer preset</span>
            <select className="tl-select" value={presetName} onChange={(e) => applyPreset(e.target.value)}>
              {presets.map((preset) => <option key={preset.name}>{preset.name}</option>)}
            </select>
          </label>
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
              min={0.1}
              step={0.5}
              value={settings.interval_seconds}
              onChange={(e) => setSettings({ ...settings, interval_seconds: Math.max(0.1, Number(e.target.value) || 0.1) })}
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
              value={settings.output_fps}
              onChange={(e) => setSettings({ ...settings, output_fps: Math.min(60, Math.max(1, Math.round(Number(e.target.value) || 1))) })}
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
        <div className="tl-advanced-toggle">
          <Button variant="ghost" size="sm" onClick={() => setAdvanced(!advanced)}>
            {advanced ? "Hide advanced settings" : "Configure capture, smoothing & analysis"}
          </Button>
          <span className="tl-meta mono">
            JPEG {settings.jpeg_quality} · {settings.auto_smooth ? `${settings.smooth_engine} auto-smooth` : "manual smooth"}
            {settings.analysis_enabled ? ` · analysis every ${settings.analysis_cadence_seconds}s` : ""}
          </span>
        </div>
        {advanced && (
          <div className="tl-advanced-grid">
            <label className="tl-field">
              <span className="microlabel">JPEG quality</span>
              <input className="tl-input" type="number" min={1} max={100} value={settings.jpeg_quality}
                onChange={(e) => setSettings({ ...settings, jpeg_quality: Math.min(100, Math.max(1, Number(e.target.value) || 1)) })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Stop after frames (0 = manual)</span>
              <input className="tl-input" type="number" min={0} value={settings.max_frames}
                onChange={(e) => setSettings({ ...settings, max_frames: Math.max(0, Math.round(Number(e.target.value) || 0)) })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Stop after seconds (0 = manual)</span>
              <input className="tl-input" type="number" min={0} value={settings.duration_seconds}
                onChange={(e) => setSettings({ ...settings, duration_seconds: Math.max(0, Number(e.target.value) || 0) })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Smoothing engine</span>
              <select className="tl-select" value={settings.smooth_engine}
                onChange={(e) => setSettings({ ...settings, smooth_engine: e.target.value as "ffmpeg" | "rife" })}>
                <option value="ffmpeg">FFmpeg optical flow</option>
                <option value="rife">RIFE, fallback to FFmpeg</option>
              </select>
            </label>
            <label className="tl-field">
              <span className="microlabel">Smooth target FPS</span>
              <input className="tl-input" type="number" min={1} max={120} value={settings.smooth_target_fps}
                onChange={(e) => setSettings({ ...settings, smooth_target_fps: Math.min(120, Math.max(1, Math.round(Number(e.target.value) || 1))) })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Output quality</span>
              <select className="tl-select" value={settings.smooth_quality}
                onChange={(e) => setSettings({ ...settings, smooth_quality: e.target.value as "standard" | "high" | "maximum" })}>
                <option value="standard">Standard</option>
                <option value="high">High</option>
                <option value="maximum">Maximum</option>
              </select>
            </label>
            <label className="tl-check">
              <input type="checkbox" checked={settings.auto_smooth}
                onChange={(e) => setSettings({ ...settings, auto_smooth: e.target.checked })} />
              <span>Automatically smooth after capture</span>
            </label>
            <label className="tl-check">
              <input type="checkbox" checked={settings.smooth_interpolate}
                onChange={(e) => setSettings({ ...settings, smooth_interpolate: e.target.checked })} />
              <span>Generate intermediate motion</span>
            </label>
            <label className="tl-check">
              <input type="checkbox" checked={settings.smooth_deflicker}
                onChange={(e) => setSettings({ ...settings, smooth_deflicker: e.target.checked })} />
              <span>Normalize exposure flicker</span>
            </label>
            <label className={`tl-check ${!ai?.enabled ? "is-disabled" : ""}`}>
              <input type="checkbox" checked={settings.analysis_enabled} disabled={!ai?.enabled}
                onChange={(e) => setSettings({ ...settings, analysis_enabled: e.target.checked })} />
              <span>Analyze printer health with local Ollama</span>
            </label>
            <label className="tl-field">
              <span className="microlabel">Analysis cadence (seconds)</span>
              <input className="tl-input" type="number" min={1} max={3600} disabled={!settings.analysis_enabled}
                value={settings.analysis_cadence_seconds}
                onChange={(e) => setSettings({ ...settings, analysis_cadence_seconds: Math.min(3600, Math.max(1, Number(e.target.value) || 1)) })} />
            </label>
          </div>
        )}
        <p className="help-foot mono">
          Raw frames are retained. Manual Smooth uses the settings above. {!ai?.enabled && "Enable Ollama on Models to turn on printer-health analysis."}
        </p>
      </div>

      {/* Active captures — live preview + stop */}
      {active.length > 0 && (
        <div className="tl-active">
          {active.map((t) => {
            const cam = cameras.find((c) => c.id === t.camera_id);
            const capturing = t.state === "capturing";
            const elapsed = (t.end_ts ?? Date.now() / 1000) - t.start_ts;
            return (
              <div key={t.id} className={`tl-live-card ${capturing ? "is-rec" : ""}`}>
                <div className="tl-live-media">
                  {cam && capturing ? (
                    <LiveViewer cam={cam} view={PREVIEW_VIEW} showOsd={false} big fit="contain" />
                  ) : (
                    <div className="tl-live-encoding">
                      <Spinner size={26} />
                      <span className="mono">Encoding video…</span>
                    </div>
                  )}
                  <div className="tl-live-chip">
                    <span className="chip-live">
                      <span className="live-dot" style={capturing ? undefined : { background: "var(--warn)" }} />
                      {capturing ? "CAPTURING" : "ENCODING"}
                    </span>
                  </div>
                </div>
                <div className="tl-live-bar">
                  <div className="tl-grow">
                    <div className="tl-name">{t.name}</div>
                    <div className="tl-meta mono">
                      {camName(t.camera_id)} · {t.frames_captured} frames · {fmtDur(elapsed)} · every {t.interval_seconds}s
                    </div>
                    {t.analysis_latest_state && HEALTH_BADGE[t.analysis_latest_state] && (
                      <span className={`badge ${HEALTH_BADGE[t.analysis_latest_state].cls}`}>
                        {HEALTH_BADGE[t.analysis_latest_state].label}
                      </span>
                    )}
                  </div>
                  {capturing ? (
                    <Button
                      variant="danger"
                      icon={<IconStop size={16} />}
                      disabled={stop.isPending}
                      onClick={() => onStop(t)}
                    >
                      Stop &amp; save
                    </Button>
                  ) : (
                    <span className="tl-encoding mono"><Spinner size={14} /> encoding…</span>
                  )}
                </div>
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
                  {t.smooth_state === "complete" && (
                    <span className="badge badge-accent tl-smooth-badge">
                      <IconSparkle size={11} /> {t.smooth_engine === "rife" ? "RIFE" : "Smooth"}
                    </span>
                  )}
                </div>
                <div className="tl-body">
                  <span className="tl-name">{t.name}</span>
                  <span className="tl-meta mono">{camName(t.camera_id)} · {fmtDateTime(t.created_ts)}</span>
                  <span className="tl-meta mono">
                    {t.frames_captured} frames
                    {t.state === "complete" && ` · ${videoSeconds(t).toFixed(1)}s @ ${t.output_fps}fps · ${fmtBytes(t.size_bytes)}`}
                  </span>
                  {t.analysis_latest_state && HEALTH_BADGE[t.analysis_latest_state] && (
                    <span className={`badge tl-health-badge ${HEALTH_BADGE[t.analysis_latest_state].cls}`}>
                      {HEALTH_BADGE[t.analysis_latest_state].label} · {t.analysis_event_count} checks
                    </span>
                  )}
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
                  {playable && t.smooth_state === "processing" && (
                    <span className="tl-encoding mono"><Spinner size={13} /> smoothing…</span>
                  )}
                  {playable && postprocess?.available && t.smooth_state !== "processing" && (
                    <Button
                      variant="outline"
                      size="sm"
                      icon={<IconSparkle size={14} />}
                      title="Interpolate frames into smooth motion"
                      onClick={() => onSmooth(t)}
                    >
                      {t.smooth_state === "complete" ? "Re-smooth" : t.smooth_state === "error" ? "Retry smooth" : "Smooth"}
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

      {play && (() => {
        const showSmooth = playSmooth && play.has_smooth;
        const videoUrl = showSmooth
          ? timelapseSmoothUrl(play.proxy_prefix, play.id)
          : timelapseFileUrl(play.proxy_prefix, play.id);
        return (
          <div className="lb-root" role="dialog" aria-modal="true" aria-label="Timelapse player">
            <div className="lb-backdrop" onClick={() => setPlay(null)} />
            <button className="lb-x" onClick={() => setPlay(null)} aria-label="Close"><IconClose size={20} /></button>
            <div className="lb-stage">
              <div className="lb-media">
                <video
                  key={videoUrl}
                  className="lb-canvas"
                  src={videoUrl}
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
                    {camName(play.camera_id)} · {play.frames_captured} frames · {videoSeconds(play).toFixed(1)}s @ {play.output_fps}fps · {fmtBytes(showSmooth ? play.smooth_size_bytes : play.size_bytes)}{showSmooth && play.smooth_engine ? ` · ${play.smooth_engine}` : ""}
                  </span>
                  {play.has_smooth && (
                    <div className="tl-player-toggle">
                      <Segmented
                        ariaLabel="Playback version"
                        value={showSmooth ? "smooth" : "original"}
                        options={[{ value: "original", label: "Original" }, { value: "smooth", label: "Smooth" }]}
                        onChange={(v) => setPlaySmooth(v === "smooth")}
                      />
                    </div>
                  )}
                </div>
                <div className="lb-actions">
                  <a className="btn btn-outline btn-sm" href={videoUrl} download>
                    <span className="btn-ic"><IconDownload size={15} /></span><span>Download</span>
                  </a>
                  <Button variant="danger" size="sm" icon={<IconTrash size={15} />} onClick={() => setConfirm(play)}>Delete</Button>
                </div>
              </div>
              {play.analysis_enabled && (
                <div className="tl-analysis-panel">
                  <div className="panel-title">Printer-health analysis</div>
                  {analysisEvents.length === 0 ? (
                    <span className="tl-meta mono">No analysis results recorded yet.</span>
                  ) : (
                    <div className="tl-analysis-events">
                      {analysisEvents.map((event) => {
                        const badge = HEALTH_BADGE[event.state];
                        return (
                          <div key={event.id} className="tl-analysis-event">
                            <img
                              className="tl-evidence"
                              src={timelapseFrameUrl(play.proxy_prefix, play.id, event.frame_number)}
                              alt={`frame ${event.frame_number}`}
                              loading="lazy"
                            />
                            <div className="tl-analysis-event-body">
                              <span className={`badge ${badge.cls}`}>{badge.label}</span>
                              <span className="mono">frame {event.frame_number} · {Math.round(event.confidence * 100)}%</span>
                              <span>{event.description}</span>
                              <span className="tl-meta mono">{fmtDateTime(event.created_ts)}</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })()}

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
