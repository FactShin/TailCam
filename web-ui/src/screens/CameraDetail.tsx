import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useCameras, useDeleteCamera, useDetectionInfo, useHosts, usePatchCamera, useRecording, useRestartCamera, useSnapshot } from "../api/hooks";
import { LiveViewer } from "../components/LiveViewer";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, ControlSlider, ScopeBadge, Segmented, Spinner, Toggle } from "../components/ui";
import {
  IconBrain,
  IconCamShutter,
  IconChevL,
  IconContrast,
  IconExpand,
  IconFlipH,
  IconFlipV,
  IconFps,
  IconGlobe,
  IconMotion,
  IconPhone,
  IconRecord,
  IconRefresh,
  IconResolution,
  IconRotate,
  IconShrink,
  IconSliders,
  IconStop,
  IconSun,
  IconTrash,
  IconZoom,
} from "../icons";
import { fmtDur } from "../lib/format";
import { versionAtLeast } from "../lib/version";
import type { CameraInfo, CameraSettingsUpdate, ViewParams } from "../types";
import { VIEW_DEFAULT } from "../types";
import { BottomSheet } from "../components/ui";

// First release whose PATCH /api/cameras/{id} accepts per-camera `stream` overrides.
const STREAM_SETTINGS_MIN_VERSION = "1.8.0";

function loadView(key: string): ViewParams {
  // Only zoom/pan are per-screen now. Older builds also stored fps/quality/w
  // here; those must NOT be replayed — they'd silently cap the stream below the
  // camera's device-wide settings.
  try {
    const stored = JSON.parse(localStorage.getItem("tailcam.view." + key) || "{}");
    return {
      zoom: typeof stored.zoom === "number" ? stored.zoom : VIEW_DEFAULT.zoom,
      panX: typeof stored.panX === "number" ? stored.panX : VIEW_DEFAULT.panX,
      panY: typeof stored.panY === "number" ? stored.panY : VIEW_DEFAULT.panY,
    };
  } catch {
    return { ...VIEW_DEFAULT };
  }
}

function useWideLayout(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia("(min-width: 1000px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1000px)");
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

export function CameraDetail() {
  const params = useParams();
  const host = decodeURIComponent(params.host || "");
  const id = decodeURIComponent(params.cid || "");
  const navigate = useNavigate();
  const toast = useToast();
  const camerasQ = useCameras();
  const cam = (camerasQ.data ?? []).find((c) => c.host === host && c.id === id);
  // Per-camera stream settings (PATCH ... {stream}) need TailCam ≥ 1.8.0 on the
  // node that owns the camera; an older peer silently ignores them.
  const hostInfo = (useHosts().data ?? []).find((h) => h.host === host);
  const legacyNodeVersion =
    hostInfo?.kind === "peer" && !versionAtLeast(hostInfo.version, STREAM_SETTINGS_MIN_VERSION)
      ? hostInfo.version || "an older version"
      : null;

  const prefix = cam?.proxy_prefix ?? "";
  const patch = usePatchCamera(prefix, id);
  const snap = useSnapshot(prefix, id);
  const rec = useRecording(prefix, id);
  const restartCam = useRestartCamera();
  const deleteCam = useDeleteCamera();

  const viewKey = `${host}/${id}`;
  const [view, setViewState] = useState<ViewParams>(() => loadView(viewKey));
  const [sheetOpen, setSheetOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [fs, setFs] = useState(false);
  const [detect, setDetect] = useState(false);
  // Plug-and-play: switch the detection overlay on by default once we know
  // detection is available — unless the user has toggled it themselves.
  const detTouched = useRef(false);
  const detInfo = useDetectionInfo().data;
  const camDetection = cam?.detection_enabled ?? false;
  useEffect(() => {
    if (!camDetection) {
      setDetect(false); // switched off for this camera → no overlay, no polling
      return;
    }
    if (!detTouched.current && detInfo?.enabled && detInfo.overlay_default) setDetect(true);
  }, [detInfo?.enabled, detInfo?.overlay_default, camDetection]);
  const stageRef = useRef<HTMLDivElement>(null);
  const wide = useWideLayout();

  useEffect(() => setViewState(loadView(viewKey)), [viewKey]);
  const setView = useCallback(
    (v: ViewParams) => {
      setViewState(v);
      try {
        localStorage.setItem("tailcam.view." + viewKey, JSON.stringify(v));
      } catch {
        /* ignore */
      }
    },
    [viewKey],
  );

  const onPatch = useCallback(
    async (update: CameraSettingsUpdate, msg?: string) => {
      try {
        await patch.mutateAsync(update);
        if (msg) toast.ok(msg);
      } catch (e) {
        toast.err(e instanceof Error ? e.message : "Update failed — reverted");
      }
    },
    [patch, toast],
  );

  const doSnapshot = async () => {
    try {
      await snap.mutateAsync();
      toast.ok("Snapshot saved", { action: { label: "View", fn: () => navigate("/gallery") } });
    } catch {
      toast.err("Snapshot failed");
    }
  };
  const toggleRecord = async () => {
    if (!cam) return;
    try {
      if (cam.recording) {
        await rec.stop.mutateAsync();
        toast.ok("Recording saved", { action: { label: "View", fn: () => navigate("/gallery") } });
      } else {
        await rec.start.mutateAsync();
        toast.ok("Recording started");
      }
    } catch {
      toast.err("Recording action failed");
    }
  };

  const doRestart = async () => {
    try {
      await restartCam.mutateAsync({ prefix, id });
      toast.ok("Camera restarting…");
    } catch {
      toast.err("Restart failed");
    }
  };
  const doDelete = async () => {
    setConfirmDelete(false);
    try {
      await deleteCam.mutateAsync({ prefix, id });
      toast.ok("Camera removed");
      navigate("/");
    } catch {
      toast.err("Delete failed");
    }
  };

  // The CSS overlay (.stage.is-fs) is the reliable fullscreen mechanism on every
  // platform — iOS Safari can't put a <div> into native fullscreen, which is why
  // the button used to do nothing on mobile. We still *try* the native API for
  // the nicer chrome-hiding where it's supported (desktop), but never depend on it.
  const toggleFs = () => {
    const el = stageRef.current;
    const next = !fs;
    setFs(next);
    try {
      if (next) {
        void el?.requestFullscreen?.().catch(() => {});
      } else if (document.fullscreenElement) {
        void document.exitFullscreen?.();
      }
    } catch {
      /* CSS overlay already applied */
    }
  };
  // Esc (or the browser exiting native fullscreen) should also drop the overlay.
  useEffect(() => {
    const on = () => {
      if (!document.fullscreenElement) setFs(false);
    };
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  // Lock background scroll while the CSS overlay is up.
  useEffect(() => {
    if (!fs) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.fullscreenElement) setFs(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", onKey);
    };
  }, [fs]);

  if (!cam) {
    if (camerasQ.isLoading) {
      return <div className="screen"><div className="empty"><Spinner size={24} /></div></div>;
    }
    return (
      <div className="screen">
        <div className="empty">
          <div className="empty-title">Camera not found</div>
          <Button variant="primary" onClick={() => navigate("/")}>Back to dashboard</Button>
        </div>
      </div>
    );
  }

  const busy = snap.isPending || rec.start.isPending || rec.stop.isPending;
  const controls = (
    <ControlsPanel
      cam={cam}
      view={view}
      setView={setView}
      onPatch={onPatch}
      patching={patch.isPending}
      legacyNodeVersion={legacyNodeVersion}
      onRestart={doRestart}
      restarting={restartCam.isPending}
      onRequestDelete={() => setConfirmDelete(true)}
    />
  );

  return (
    <div className={`detail ${wide ? "detail-side" : "detail-stack"}`}>
      <div className="detail-main">
        <div className="detail-top">
          <button className="back-btn" onClick={() => navigate("/")} aria-label="Back"><IconChevL size={18} /></button>
          <div className="detail-id">
            <h1 className="detail-name">{cam.name}</h1>
            <span className="detail-meta mono">{cam.host} · {cam.id} · {cam.backend}</span>
            {cam.status === "offline" && cam.last_error && (
              <span className="detail-error">{cam.last_error}</span>
            )}
          </div>
        </div>

        <div ref={stageRef} className={`stage ${fs ? "is-fs" : ""}`}>
          <LiveViewer cam={cam} view={view} onView={setView} big interactive showUrl fit="contain" detect={detect} />
          <button
            className={`fs-btn detect-btn ${detect ? "is-on" : ""}`}
            onClick={() => { detTouched.current = true; setDetect((d) => !d); }}
            aria-label="Toggle object detection"
            title="Object detection overlay (active model)"
          >
            <IconBrain size={18} />
          </button>
          <button className="fs-btn" onClick={toggleFs} aria-label="Fullscreen">
            {fs ? <IconShrink size={18} /> : <IconExpand size={18} />}
          </button>
          {view.zoom > 1.02 && (
            <button className="resetview-btn" onClick={() => setView({ ...view, zoom: 1, panX: 0.5, panY: 0.5 })}>
              Reset zoom
            </button>
          )}
        </div>

        <div className="action-bar">
          <button className="action snap" onClick={doSnapshot} disabled={busy || cam.status === "offline"} aria-label="Take snapshot">
            {snap.isPending ? <Spinner size={22} /> : <IconCamShutter size={24} />}
            <span>Snapshot</span>
          </button>
          <button
            className={`action rec ${cam.recording ? "is-rec" : ""}`}
            onClick={toggleRecord}
            disabled={busy || cam.status === "offline"}
            aria-label={cam.recording ? "Stop recording" : "Start recording"}
          >
            {rec.start.isPending || rec.stop.isPending ? <Spinner size={22} /> : cam.recording ? <IconStop size={22} /> : <IconRecord size={22} />}
            <span>{cam.recording ? "Stop" : "Record"}</span>
          </button>
          {!wide && (
            <button className="action ctrls" onClick={() => setSheetOpen(true)} aria-label="Open controls">
              <IconSliders size={24} /><span>Controls</span>
            </button>
          )}
        </div>
        <div className="hint-pinch mono">Pinch / scroll to zoom · drag to pan — updates are debounced per tab</div>
      </div>

      {wide ? (
        <aside className="detail-aside">{controls}</aside>
      ) : (
        <BottomSheet open={sheetOpen} onClose={() => setSheetOpen(false)} title="Controls">
          {controls}
        </BottomSheet>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="Remove this camera?"
        confirmLabel="Remove"
        body={`"${cam.name}" will be removed and hidden from this device. You can bring it back with "Restore hidden" on the dashboard.`}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={doDelete}
      />
    </div>
  );
}

function ControlsPanel({
  cam,
  view,
  setView,
  onPatch,
  patching,
  legacyNodeVersion,
  onRestart,
  restarting,
  onRequestDelete,
}: {
  cam: CameraInfo;
  view: ViewParams;
  setView: (v: ViewParams) => void;
  onPatch: (u: CameraSettingsUpdate, msg?: string) => void;
  patching: boolean;
  // Set when the owning peer is too old for per-camera stream settings.
  legacyNodeVersion: string | null;
  onRestart: () => void;
  restarting: boolean;
  onRequestDelete: () => void;
}) {
  const RES = [
    { value: "640x480", label: "640×480" },
    { value: "1280x720", label: "720p" },
    { value: "1920x1080", label: "1080p" },
  ];
  const resVal = `${cam.width}x${cam.height}`;
  const [name, setName] = useState(cam.name);
  const [img, setImg] = useState({
    brightness: (cam.properties.brightness as number) ?? 50,
    contrast: (cam.properties.contrast as number) ?? 50,
  });
  useEffect(() => setName(cam.name), [cam.name]);
  useEffect(
    () =>
      setImg({
        brightness: (cam.properties.brightness as number) ?? 50,
        contrast: (cam.properties.contrast as number) ?? 50,
      }),
    [cam.properties.brightness, cam.properties.contrast],
  );
  // Device-wide stream settings (saved on the camera's node — every viewer,
  // every device). Sliders edit locally and commit on release.
  const [stream, setStream] = useState({ fps: cam.stream.fps, quality: cam.stream.quality });
  useEffect(
    () => setStream({ fps: cam.stream.fps, quality: cam.stream.quality }),
    [cam.stream.fps, cam.stream.quality],
  );
  const hasStreamOverride =
    cam.stream_overrides.fps != null || cam.stream_overrides.quality != null || cam.stream_overrides.max_width != null;
  const commitStream = (key: "fps" | "quality") => {
    if (stream[key] === cam.stream[key]) return;
    onPatch({ stream: { [key]: stream[key] } }, key === "fps" ? `Stream → ${stream.fps} fps` : `Quality → ${stream.quality}%`);
  };

  const setRes = (v: string) => {
    const [w, h] = v.split("x").map(Number);
    onPatch({ properties: { width: w, height: h } }, `Resolution → ${v.replace("x", "×")}`);
  };

  return (
    <div className="controls">
      <section className="ctl-sec ctl-local">
        <header className="ctl-head">
          <div className="ctl-head-l"><IconPhone size={16} /><span>This view</span></div>
          <ScopeBadge scope="local" />
        </header>
        <p className="ctl-note">Zoom and pan are a viewing gesture for this screen only. Frame rate, quality, and size are camera settings below.</p>
        <ControlSlider label="Zoom" icon={<IconZoom size={14} />} value={view.zoom} min={1} max={8} step={0.1} format={(v) => v.toFixed(1) + "×"}
          onChange={(v) => setView({ ...view, zoom: v, panX: v <= 1 ? 0.5 : view.panX, panY: v <= 1 ? 0.5 : view.panY })} />
        <div className="ctl-pan">
          <ControlSlider label="Pan X" value={view.panX} min={0} max={1} step={0.01} disabled={view.zoom <= 1} format={(v) => Math.round(v * 100) + "%"}
            onChange={(v) => setView({ ...view, panX: v })} />
          <ControlSlider label="Pan Y" value={view.panY} min={0} max={1} step={0.01} disabled={view.zoom <= 1} format={(v) => Math.round(v * 100) + "%"}
            onChange={(v) => setView({ ...view, panY: v })} />
        </div>
        <button className="ctl-reset" onClick={() => setView({ ...VIEW_DEFAULT })}>Reset zoom</button>
      </section>

      <section className="ctl-sec ctl-global">
        <header className="ctl-head">
          <div className="ctl-head-l"><IconGlobe size={16} /><span>Camera settings</span></div>
          <ScopeBadge scope="global" />
        </header>
        <p className="ctl-note">Saved on the camera's device — one setting for <strong>everyone</strong>, on every screen.</p>

        <div className="ctl-row">
          <span className="ctl-row-label"><IconResolution size={14} /> Resolution</span>
          <Segmented ariaLabel="Resolution" value={resVal} options={RES} onChange={(v) => setRes(v as string)} />
        </div>
        {legacyNodeVersion ? (
          <p className="ctl-note">
            This node runs TailCam {legacyNodeVersion}; update it to manage stream settings here.
          </p>
        ) : (
          <>
            <ControlSlider label="Stream frame rate" icon={<IconFps size={14} />} value={stream.fps} min={1} max={60} unit=" fps"
              onChange={(v) => setStream((s) => ({ ...s, fps: v }))}
              onCommit={() => commitStream("fps")} />
            <ControlSlider label="Stream quality" icon={<IconSliders size={14} />} value={stream.quality} min={1} max={100} unit="%"
              onChange={(v) => setStream((s) => ({ ...s, quality: v }))}
              onCommit={() => commitStream("quality")} />
            <div className="ctl-row">
              <span className="ctl-row-label"><IconResolution size={14} /> Stream max width</span>
              <Segmented ariaLabel="Stream max width" value={cam.stream.max_width}
                options={[{ value: 0, label: "Native" }, { value: 640, label: "640" }, { value: 960, label: "960" }, { value: 1280, label: "1280" }]}
                onChange={(v) => onPatch({ stream: { max_width: v as number } }, `Stream width → ${v || "native"}`)} />
            </div>
            <div className="ctl-row ctl-row-split">
              <span className="ctl-note" style={{ margin: 0 }}>
                {hasStreamOverride
                  ? "Overriding the global streaming defaults for this camera."
                  : "Using the global streaming defaults (Settings → Streaming)."}
              </span>
              {hasStreamOverride && (
                <button className="ctl-reset" onClick={() => onPatch({ stream: { fps: null, quality: null, max_width: null } }, "Using global defaults")}>
                  Use global defaults
                </button>
              )}
            </div>
          </>
        )}
        <div className="ctl-row">
          <span className="ctl-row-label"><IconRotate size={14} /> Rotation</span>
          <Segmented ariaLabel="Rotation" value={cam.transform.rotation}
            options={[{ value: 0, label: "0°" }, { value: 90, label: "90°" }, { value: 180, label: "180°" }, { value: 270, label: "270°" }]}
            onChange={(v) => onPatch({ transform: { ...cam.transform, rotation: v as number } }, `Rotation → ${v}°`)} />
        </div>
        <div className="ctl-row ctl-row-split">
          <button className={`flip-btn ${cam.transform.flip_h ? "is-on" : ""}`} onClick={() => onPatch({ transform: { ...cam.transform, flip_h: !cam.transform.flip_h } }, "Flipped horizontally")}>
            <IconFlipH size={16} /> Flip H
          </button>
          <button className={`flip-btn ${cam.transform.flip_v ? "is-on" : ""}`} onClick={() => onPatch({ transform: { ...cam.transform, flip_v: !cam.transform.flip_v } }, "Flipped vertically")}>
            <IconFlipV size={16} /> Flip V
          </button>
        </div>
        <ControlSlider label="Brightness" icon={<IconSun size={14} />} value={img.brightness} min={0} max={100}
          onChange={(v) => setImg((s) => ({ ...s, brightness: v }))} onCommit={() => onPatch({ properties: { brightness: img.brightness } }, "Brightness updated")} />
        <ControlSlider label="Contrast" icon={<IconContrast size={14} />} value={img.contrast} min={0} max={100}
          onChange={(v) => setImg((s) => ({ ...s, contrast: v }))} onCommit={() => onPatch({ properties: { contrast: img.contrast } }, "Contrast updated")} />
        <div className="ctl-row">
          <span className="ctl-row-label"><IconMotion size={14} /> Motion detection</span>
          <Toggle checked={cam.motion_enabled} label="Motion detection" onChange={(v) => onPatch({ motion_enabled: v }, v ? "Motion detection on" : "Motion detection off")} />
        </div>
        <div className="ctl-row">
          <span className="ctl-row-label"><IconBrain size={14} /> Object detection</span>
          <Toggle checked={cam.detection_enabled} label="Object detection" onChange={(v) => onPatch({ detection_enabled: v }, v ? "Object detection on for this camera" : "Object detection off for this camera")} />
        </div>
        {cam.detection_override !== null && (
          <div className="ctl-row ctl-row-split">
            <span className="ctl-note" style={{ margin: 0 }}>Overriding the global object-detection switch (AI Studio).</span>
            <button className="ctl-reset" onClick={() => onPatch({ clear_detection_override: true }, "Following the global setting")}>Follow global</button>
          </div>
        )}
        <div className="ctl-rename">
          <span className="ctl-row-label">Camera name</span>
          <div className="rename-row">
            <input className="text-in" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} aria-label="Camera name" />
            <Button variant="primary" size="sm" disabled={name.trim() === cam.name || !name.trim() || patching} onClick={() => onPatch({ name: name.trim() }, "Renamed")}>Save</Button>
          </div>
        </div>
        {patching && <div className="ctl-saving mono"><Spinner size={12} /> saving…</div>}
      </section>

      <section className="ctl-sec">
        <header className="ctl-head">
          <div className="ctl-head-l"><IconSliders size={16} /><span>Maintenance</span></div>
        </header>
        <div className="ctl-row ctl-row-split">
          <Button variant="outline" onClick={onRestart} disabled={restarting}>
            {restarting ? <Spinner size={14} /> : <IconRefresh size={15} />} Restart feed
          </Button>
          <Button variant="danger" icon={<IconTrash size={15} />} onClick={onRequestDelete}>
            Remove camera
          </Button>
        </div>
        <p className="ctl-note">Restart recovers a stuck feed. Remove hides this camera from the dashboard.</p>
      </section>
    </div>
  );
}
