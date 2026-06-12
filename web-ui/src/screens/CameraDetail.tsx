import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useCameras, useDeleteCamera, usePatchCamera, useRecording, useRestartCamera, useSnapshot } from "../api/hooks";
import { LiveViewer } from "../components/LiveViewer";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, ControlSlider, ScopeBadge, Segmented, Spinner, Toggle } from "../components/ui";
import {
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
import type { CameraInfo, CameraSettingsUpdate, ViewParams } from "../types";
import { VIEW_DEFAULT } from "../types";
import { BottomSheet } from "../components/ui";

function loadView(key: string): ViewParams {
  try {
    return { ...VIEW_DEFAULT, ...JSON.parse(localStorage.getItem("tailcam.view." + key) || "{}") };
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

  const toggleFs = () => {
    const el = stageRef.current;
    if (!document.fullscreenElement) {
      el?.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.();
    }
  };
  useEffect(() => {
    const on = () => setFs(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);

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
          <LiveViewer cam={cam} view={view} onView={setView} big interactive showUrl fit="contain" />
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
  onRestart,
  restarting,
  onRequestDelete,
}: {
  cam: CameraInfo;
  view: ViewParams;
  setView: (v: ViewParams) => void;
  onPatch: (u: CameraSettingsUpdate, msg?: string) => void;
  patching: boolean;
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

  const setRes = (v: string) => {
    const [w, h] = v.split("x").map(Number);
    onPatch({ properties: { width: w, height: h } }, `Resolution → ${v.replace("x", "×")}`);
  };

  return (
    <div className="controls">
      <section className="ctl-sec ctl-local">
        <header className="ctl-head">
          <div className="ctl-head-l"><IconPhone size={16} /><span>My view</span></div>
          <ScopeBadge scope="local" />
        </header>
        <p className="ctl-note">Only changes this tab's stream — others are unaffected.</p>
        <ControlSlider label="Frame rate" icon={<IconFps size={14} />} value={view.fps} min={1} max={60} unit=" fps"
          onChange={(v) => setView({ ...view, fps: v })} />
        <ControlSlider label="Zoom" icon={<IconZoom size={14} />} value={view.zoom} min={1} max={8} step={0.1} format={(v) => v.toFixed(1) + "×"}
          onChange={(v) => setView({ ...view, zoom: v, panX: v <= 1 ? 0.5 : view.panX, panY: v <= 1 ? 0.5 : view.panY })} />
        <div className="ctl-pan">
          <ControlSlider label="Pan X" value={view.panX} min={0} max={1} step={0.01} disabled={view.zoom <= 1} format={(v) => Math.round(v * 100) + "%"}
            onChange={(v) => setView({ ...view, panX: v })} />
          <ControlSlider label="Pan Y" value={view.panY} min={0} max={1} step={0.01} disabled={view.zoom <= 1} format={(v) => Math.round(v * 100) + "%"}
            onChange={(v) => setView({ ...view, panY: v })} />
        </div>
        <ControlSlider label="Quality" icon={<IconSliders size={14} />} value={view.quality} min={1} max={100} unit="%"
          onChange={(v) => setView({ ...view, quality: v })} />
        <div className="ctl-row">
          <span className="ctl-row-label"><IconResolution size={14} /> Max width</span>
          <Segmented ariaLabel="Max width" value={view.w}
            options={[{ value: 0, label: "Native" }, { value: 480, label: "480" }, { value: 854, label: "854" }, { value: 1280, label: "1280" }]}
            onChange={(v) => setView({ ...view, w: v as number })} />
        </div>
        <button className="ctl-reset" onClick={() => setView({ ...VIEW_DEFAULT })}>Reset my view</button>
      </section>

      <section className="ctl-sec ctl-global">
        <header className="ctl-head">
          <div className="ctl-head-l"><IconGlobe size={16} /><span>Camera settings</span></div>
          <ScopeBadge scope="global" />
        </header>
        <p className="ctl-note">Saved on the device — changes what <strong>everyone</strong> sees.</p>

        <div className="ctl-row">
          <span className="ctl-row-label"><IconResolution size={14} /> Resolution</span>
          <Segmented ariaLabel="Resolution" value={resVal} options={RES} onChange={(v) => setRes(v as string)} />
        </div>
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
