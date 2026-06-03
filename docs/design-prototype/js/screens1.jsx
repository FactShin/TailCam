// screens1.jsx — Dashboard + CameraDetail (mobile-first, global-vs-local controls)

const { useState: useS1, useEffect: useE1, useRef: useR1, useCallback: useC1 } = React;

// persist per-camera local view params
const VIEW_DEFAULT = { fps: 15, zoom: 1, panX: 0.5, panY: 0.5, quality: 75, w: 0 };
function loadView(id) {
  try { return { ...VIEW_DEFAULT, ...JSON.parse(localStorage.getItem("anycam.view." + id) || "{}") }; }
  catch { return { ...VIEW_DEFAULT }; }
}

// ===========================================================================
// Dashboard
// ===========================================================================
function Dashboard({ tile }) {
  const cameras = useCameras();
  const nav = window.useNavigate();
  const toast = useToast();
  const [refreshing, setRefreshing] = useS1(false);
  const [loading, setLoading] = useS1(true);
  useE1(() => { const t = setTimeout(() => setLoading(false), 350); return () => clearTimeout(t); }, []);

  const refresh = async () => {
    setRefreshing(true);
    try { await api.refresh(); toast.ok("Devices re-scanned"); }
    catch { toast.err("Re-scan failed"); }
    finally { setRefreshing(false); }
  };

  const online = cameras.filter((c) => c.status === "online").length;

  if (loading) {
    return (
      <div className="screen">
        <div className="grid" data-tile={tile}>
          {[0, 1, 2, 3].map((i) => <div key={i} className="tile tile-skeleton" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <h1 className="screen-title">Cameras</h1>
          <p className="screen-sub">
            {cameras.length} device{cameras.length !== 1 ? "s" : ""} · <span style={{ color: "var(--ok)" }}>{online} online</span>
            <span className="bw-note" title="Grid tiles request a low-bandwidth stream (fps 8, 480px) and pause when off-screen or the tab is hidden.">
              <IconFps size={13} /> low-bandwidth grid
            </span>
          </p>
        </div>
        <Button variant="outline" icon={<IconRefresh size={16} className={refreshing ? "spin" : ""} />} onClick={refresh} disabled={refreshing}>
          {refreshing ? "Scanning…" : "Refresh devices"}
        </Button>
      </div>

      {cameras.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconCamera size={40} /></div>
          <div className="empty-title">No cameras found</div>
          <div className="empty-sub">Plug in a USB camera or webcam on the host, then re-scan devices.</div>
          <Button variant="primary" icon={<IconRefresh size={16} />} onClick={refresh}>Refresh devices</Button>
        </div>
      ) : (
        <div className="grid" data-tile={tile}>
          {cameras.map((c) => (
            <CameraTile key={c.id} cam={c} layout={tile} onOpen={() => nav(`/camera/${c.id}`)} />
          ))}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Controls panel (shared by side panel + bottom sheet)
// ===========================================================================
function ControlsPanel({ cam, view, setView, onPatch, patching }) {
  const RES = [
    { value: "640x480", label: "640×480" },
    { value: "1280x720", label: "720p" },
    { value: "1920x1080", label: "1080p" },
  ];
  const resVal = `${cam.width}x${cam.height}`;
  const [name, setName] = useS1(cam.name);
  useE1(() => setName(cam.name), [cam.name]);

  // local-optimistic image sliders; PATCH only on release (no per-tick network)
  const [img, setImg] = useS1({ brightness: cam.properties.brightness ?? 50, contrast: cam.properties.contrast ?? 50 });
  useE1(() => setImg({ brightness: cam.properties.brightness ?? 50, contrast: cam.properties.contrast ?? 50 }), [cam.properties.brightness, cam.properties.contrast]);

  const setRes = (v) => { const [w, h] = v.split("x").map(Number); onPatch({ properties: { width: w, height: h } }, `Resolution → ${v.replace("x", "×")}`); };

  return (
    <div className="controls">
      {/* LOCAL — My view */}
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
            onChange={(v) => setView({ ...view, w: v })} />
        </div>
        <button className="ctl-reset" onClick={() => setView({ ...VIEW_DEFAULT })}>Reset my view</button>
      </section>

      {/* GLOBAL — Camera settings */}
      <section className="ctl-sec ctl-global">
        <header className="ctl-head">
          <div className="ctl-head-l"><IconGlobe size={16} /><span>Camera settings</span></div>
          <ScopeBadge scope="global" />
        </header>
        <p className="ctl-note">Saved on the device — changes what <strong>everyone</strong> sees.</p>

        <div className="ctl-row">
          <span className="ctl-row-label"><IconResolution size={14} /> Resolution</span>
          <Segmented ariaLabel="Resolution" value={resVal} options={RES} onChange={setRes} />
        </div>
        <div className="ctl-row">
          <span className="ctl-row-label"><IconRotate size={14} /> Rotation</span>
          <Segmented ariaLabel="Rotation" value={cam.transform.rotation}
            options={[{ value: 0, label: "0°" }, { value: 90, label: "90°" }, { value: 180, label: "180°" }, { value: 270, label: "270°" }]}
            onChange={(v) => onPatch({ transform: { ...cam.transform, rotation: v } }, `Rotation → ${v}°`)} />
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
    </div>
  );
}

// ===========================================================================
// CameraDetail
// ===========================================================================
function CameraDetail({ id, detailLayout }) {
  const cam = useCamera(id);
  const nav = window.useNavigate();
  const toast = useToast();
  const [view, setViewState] = useS1(() => loadView(id));
  const [sheetOpen, setSheetOpen] = useS1(false);
  const [patching, setPatching] = useS1(false);
  const [busy, setBusy] = useS1(null); // 'snap' | 'rec'
  const [fs, setFs] = useS1(false);
  const wrapRef = useR1(null);

  useE1(() => { setViewState(loadView(id)); }, [id]);
  const setView = useC1((v) => {
    setViewState(v);
    try { localStorage.setItem("anycam.view." + id, JSON.stringify(v)); } catch {}
  }, [id]);

  // optimistic global PATCH with revert
  const onPatch = useC1(async (update, msg) => {
    setPatching(true);
    try {
      await api.patchCamera(id, update);
      if (msg) toast.ok(msg);
    } catch (e) {
      toast.err((e && e.message) || "Update failed — reverted");
    } finally { setPatching(false); }
  }, [id, toast]);

  const doSnapshot = async () => {
    setBusy("snap");
    try { await api.snapshot(id); toast.ok("Snapshot saved", { action: { label: "View", fn: () => nav("/gallery") } }); }
    catch { toast.err("Snapshot failed"); }
    finally { setBusy(null); }
  };
  const toggleRecord = async () => {
    setBusy("rec");
    try {
      if (cam.recording) { await api.stopRecording(id); toast.ok("Recording saved", { action: { label: "View", fn: () => nav("/gallery") } }); }
      else { await api.startRecording(id); toast.ok("Recording started"); }
    } catch { toast.err("Recording action failed"); }
    finally { setBusy(null); }
  };

  const toggleFs = () => {
    const el = wrapRef.current;
    if (!document.fullscreenElement) { el && el.requestFullscreen && el.requestFullscreen().catch(() => {}); setFs(true); }
    else { document.exitFullscreen && document.exitFullscreen(); setFs(false); }
  };
  useE1(() => {
    const on = () => setFs(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);

  if (!cam) {
    return (
      <div className="screen">
        <div className="empty">
          <div className="empty-title">Camera not found</div>
          <Button variant="primary" onClick={() => nav("/")}>Back to dashboard</Button>
        </div>
      </div>
    );
  }

  const sideLayout = detailLayout === "side";
  const controls = <ControlsPanel cam={cam} view={view} setView={setView} onPatch={onPatch} patching={patching} />;

  return (
    <div className={`detail ${sideLayout ? "detail-side" : "detail-stack"}`}>
      <div className="detail-main">
        <div className="detail-top">
          <button className="back-btn" onClick={() => nav("/")} aria-label="Back"><IconChevL size={18} /></button>
          <div className="detail-id">
            <h1 className="detail-name">{cam.name}</h1>
            <span className="detail-meta mono">{cam.id} · {cam.backend}</span>
          </div>
          <StatusPill status={cam.status} fps={cam.fps} />
        </div>

        <div ref={wrapRef} className={`stage ${fs ? "is-fs" : ""}`}>
          <LiveViewer cam={cam} view={view} onView={setView} big interactive showUrl fit="contain" />
          <button className="fs-btn" onClick={toggleFs} aria-label="Fullscreen">{fs ? <IconShrink size={18} /> : <IconExpand size={18} />}</button>
          {view.zoom > 1.02 && <button className="resetview-btn" onClick={() => setView({ ...view, zoom: 1, panX: 0.5, panY: 0.5 })}>Reset zoom</button>}
        </div>

        {/* Thumb-reachable primary actions */}
        <div className="action-bar">
          <button className="action snap" onClick={doSnapshot} disabled={!!busy || cam.status === "offline"} aria-label="Take snapshot">
            {busy === "snap" ? <Spinner size={22} /> : <IconCamShutter size={24} />}
            <span>Snapshot</span>
          </button>
          <button className={`action rec ${cam.recording ? "is-rec" : ""}`} onClick={toggleRecord} disabled={!!busy || cam.status === "offline"} aria-label={cam.recording ? "Stop recording" : "Start recording"}>
            {busy === "rec" ? <Spinner size={22} /> : cam.recording ? <IconStop size={22} /> : <IconRecord size={22} />}
            <span>{cam.recording ? `Stop · ${fmtDur((Date.now() / 1000) - (window._recStart?.[id] || Date.now() / 1000))}` : "Record"}</span>
          </button>
          {!sideLayout && (
            <button className="action ctrls" onClick={() => setSheetOpen(true)} aria-label="Open controls">
              <IconSliders size={24} /><span>Controls</span>
            </button>
          )}
        </div>
        <div className="hint-pinch mono">Pinch / scroll to zoom · drag to pan — updates are debounced per tab</div>
      </div>

      {sideLayout ? (
        <aside className="detail-aside">{controls}</aside>
      ) : (
        <BottomSheet open={sheetOpen} onClose={() => setSheetOpen(false)} title="Controls">
          {controls}
        </BottomSheet>
      )}
    </div>
  );
}

Object.assign(window, { Dashboard, CameraDetail, ControlsPanel, VIEW_DEFAULT, loadView });
window._recStart = window._recStart || {};
