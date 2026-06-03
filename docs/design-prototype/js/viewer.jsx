// viewer.jsx — LiveViewer (synthetic MJPEG canvas + gesture zoom/pan + reconnect),
// CameraTile (low-bandwidth grid tile with status), and a tiny gesture hook.

const { useState: useStateV, useEffect: useEffectV, useRef: useRefV, useCallback: useCallbackV } = React;

const _recStart = {}; // camera id -> epoch start (for REC timer)

// Debounce util
function useDebounced(value, ms) {
  const [v, setV] = useStateV(value);
  useEffectV(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [JSON.stringify(value), ms]);
  return v;
}

// LiveViewer ---------------------------------------------------------------
function LiveViewer({ cam, view, onView, big = false, interactive = false, showOsd = true, showUrl = false, fit = "cover", onMotion }) {
  const wrapRef = useRefV(null);
  const canvasRef = useRefV(null);
  const [visible, setVisible] = useStateV(true);
  const [reconnect, setReconnect] = useStateV(cam.status === "offline");
  const [backoff, setBackoff] = useStateV(2);
  const [stamp, setStamp] = useStateV(osdStamp());
  const [recElapsed, setRecElapsed] = useStateV(0);
  const motionRef = useRefV(0);
  const pageVisible = usePageVisible();

  // committed (debounced) view — emulates the debounced MJPEG src update
  const debouncedView = useDebounced(view, 260);

  // Pause drawing when offscreen (low-bandwidth intent) -------------------
  useEffectV(() => {
    const el = wrapRef.current;
    if (!el || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { threshold: 0.05 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // REC timer
  useEffectV(() => {
    if (cam.recording) {
      if (!_recStart[cam.id]) _recStart[cam.id] = Date.now() / 1000;
    } else { delete _recStart[cam.id]; setRecElapsed(0); }
  }, [cam.recording, cam.id]);

  // Offline reconnect with backoff (mock never recovers for the offline cam,
  // but the backoff/cache-buster cycle is exactly the real strategy).
  useEffectV(() => {
    if (cam.status !== "offline") { setReconnect(false); return; }
    setReconnect(true);
    let b = 2, alive = true;
    const tick = () => {
      if (!alive) return;
      setBackoff(b);
      const t = setTimeout(() => { b = Math.min(30, b * 1.6); tick(); }, b * 1000);
      timer.t = t;
    };
    const timer = {};
    tick();
    return () => { alive = false; clearTimeout(timer.t); };
  }, [cam.status]);

  // Draw loop — throttled to the view fps; paused when hidden/offscreen.
  useEffectV(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const active = (pageVisible && visible) || big;
    const targetFps = cam.status === "offline" ? 0 : (cam.status === "degraded" ? Math.min(view.fps, 8) : view.fps);
    let raf, last = 0, alive = true;
    const sceneSpeedT0 = performance.now();

    const sizeCanvas = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(2, Math.round(r.width * dpr));
      const h = Math.max(2, Math.round(r.height * dpr));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    };

    const drawOnce = (force) => {
      sizeCanvas();
      const t = (performance.now() - sceneSpeedT0) / 1000;
      const m = renderFeed(canvas, cam, {
        t,
        zoom: view.zoom, panX: view.panX, panY: view.panY,
        brightness: cam.properties.brightness ?? 50,
        contrast: cam.properties.contrast ?? 50,
        saturation: cam.properties.saturation ?? 50,
        rotation: cam.transform.rotation, flipH: cam.transform.flip_h, flipV: cam.transform.flip_v,
        glitch: cam.status === "degraded",
        dim: cam.status === "offline",
      });
      motionRef.current = m;
      if (onMotion) onMotion(m);
    };

    if (cam.status === "offline") { drawOnce(true); return () => { alive = false; }; }

    const loop = (ts) => {
      if (!alive) return;
      raf = requestAnimationFrame(loop);
      if (!active) return;
      const interval = 1000 / Math.max(1, targetFps);
      if (ts - last < interval) return;
      last = ts;
      // degraded: random dropped frames
      if (cam.status === "degraded" && Math.random() < 0.18) return;
      drawOnce();
    };
    raf = requestAnimationFrame(loop);
    return () => { alive = false; cancelAnimationFrame(raf); };
  }, [cam, view.fps, view.zoom, view.panX, view.panY, visible, pageVisible, big]);

  // OSD clock + rec elapsed
  useEffectV(() => {
    const t = setInterval(() => {
      setStamp(osdStamp());
      if (cam.recording && _recStart[cam.id]) setRecElapsed(Date.now() / 1000 - _recStart[cam.id]);
    }, 1000);
    return () => clearInterval(t);
  }, [cam.recording, cam.id]);

  // Gestures (detail only) -------------------------------------------------
  const gesture = useRefV({ dragging: false, sx: 0, sy: 0, pPanX: 0.5, pPanY: 0.5, pinchD: 0, pZoom: 1 });
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

  const onWheel = useCallbackV((e) => {
    if (!interactive) return;
    e.preventDefault();
    const nz = clamp(+(view.zoom * (e.deltaY < 0 ? 1.12 : 0.89)).toFixed(2), 1, 8);
    onView({ ...view, zoom: nz, panX: nz <= 1 ? 0.5 : view.panX, panY: nz <= 1 ? 0.5 : view.panY });
  }, [interactive, view, onView]);

  const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  const onTouchStart = useCallbackV((e) => {
    if (!interactive) return;
    const g = gesture.current;
    if (e.touches.length === 2) { g.pinchD = dist(e.touches); g.pZoom = view.zoom; }
    else if (e.touches.length === 1 && view.zoom > 1) {
      g.dragging = true; g.sx = e.touches[0].clientX; g.sy = e.touches[0].clientY; g.pPanX = view.panX; g.pPanY = view.panY;
    }
  }, [interactive, view]);
  const onTouchMove = useCallbackV((e) => {
    if (!interactive) return;
    const g = gesture.current;
    if (e.touches.length === 2 && g.pinchD) {
      e.preventDefault();
      const nz = clamp(+(g.pZoom * (dist(e.touches) / g.pinchD)).toFixed(2), 1, 8);
      onView({ ...view, zoom: nz });
    } else if (g.dragging && e.touches.length === 1) {
      e.preventDefault();
      const rect = wrapRef.current.getBoundingClientRect();
      const dx = (e.touches[0].clientX - g.sx) / rect.width;
      const dy = (e.touches[0].clientY - g.sy) / rect.height;
      const k = 1 / view.zoom;
      onView({ ...view, panX: clamp(g.pPanX - dx * k, 0, 1), panY: clamp(g.pPanY - dy * k, 0, 1) });
    }
  }, [interactive, view, onView]);
  const onTouchEnd = useCallbackV(() => { const g = gesture.current; g.dragging = false; g.pinchD = 0; }, []);

  // mouse drag pan (desktop)
  const onMouseDown = useCallbackV((e) => {
    if (!interactive || view.zoom <= 1) return;
    const g = gesture.current; g.dragging = true; g.sx = e.clientX; g.sy = e.clientY; g.pPanX = view.panX; g.pPanY = view.panY;
  }, [interactive, view]);
  useEffectV(() => {
    if (!interactive) return;
    const move = (e) => {
      const g = gesture.current; if (!g.dragging) return;
      const rect = wrapRef.current.getBoundingClientRect();
      const dx = (e.clientX - g.sx) / rect.width, dy = (e.clientY - g.sy) / rect.height, k = 1 / view.zoom;
      onView({ ...view, panX: clamp(g.pPanX - dx * k, 0, 1), panY: clamp(g.pPanY - dy * k, 0, 1) });
    };
    const up = () => { gesture.current.dragging = false; };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, [interactive, view, onView]);

  // built (debounced) MJPEG url for the inspect chip
  const builtUrl = streamUrl(cam.id, {
    fps: Math.round(debouncedView.fps), zoom: debouncedView.zoom.toFixed(1),
    pan_x: debouncedView.panX.toFixed(2), pan_y: debouncedView.panY.toFixed(2),
    w: debouncedView.w || undefined, q: debouncedView.quality,
  });

  return (
    <div ref={wrapRef} className={`viewer ${big ? "viewer-big" : ""} ${interactive ? "viewer-grab" : ""}`}
      onWheel={onWheel} onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} onMouseDown={onMouseDown}
      style={{ cursor: interactive && view.zoom > 1 ? (gesture.current.dragging ? "grabbing" : "grab") : "default" }}>
      <canvas ref={canvasRef} className="viewer-canvas" style={{ objectFit: fit }} />

      {/* OSD timestamp (burned-in look) */}
      {showOsd && cam.status !== "offline" && (
        <div className="osd mono">{stamp}</div>
      )}

      {/* live + rec chips */}
      {cam.status !== "offline" && (
        <div className="viewer-tl">
          <span className="chip-live"><span className="live-dot" />LIVE</span>
          {cam.recording && <span className="chip-rec"><span className="rec-dot" />REC {fmtDur(recElapsed)}</span>}
        </div>
      )}

      {/* zoom indicator */}
      {interactive && view.zoom > 1.02 && (
        <div className="viewer-zoomchip mono"><IconZoom size={13} /> {view.zoom.toFixed(1)}×</div>
      )}

      {/* offline / reconnecting */}
      {reconnect && (
        <div className="viewer-overlay">
          <div className="overlay-inner">
            <Spinner size={26} />
            <div className="overlay-title">Offline · reconnecting</div>
            <div className="overlay-sub mono">retry in {backoff < 10 ? backoff.toFixed(0) : Math.round(backoff)}s · last frame shown</div>
          </div>
        </div>
      )}

      {/* debounced stream URL inspector (design intent: per-tab params) */}
      {showUrl && (
        <div className="viewer-url mono" title="The MJPEG <img> src — these params are per-tab only">
          GET {builtUrl}
        </div>
      )}
    </div>
  );
}

// CameraTile ---------------------------------------------------------------
function CameraTile({ cam, layout = "cinematic", onOpen }) {
  const [motion, setMotion] = useStateV(0);
  const lowBw = { fps: 8, zoom: 1, panX: 0.5, panY: 0.5, quality: 55, w: 480 };
  const motionOn = cam.motion_enabled && motion > 0.5 && cam.status !== "offline";

  const statusBits = (
    <>
      {cam.motion_enabled && (
        <span className={`tile-motion ${motionOn ? "is-active" : ""}`} title={motionOn ? "Motion detected" : "Motion armed"}>
          <IconMotion size={15} />
        </span>
      )}
      {cam.recording && <span className="tile-rec" title="Recording"><span className="rec-dot" /></span>}
    </>
  );

  if (layout === "compact") {
    return (
      <button className="tile tile-compact" onClick={onOpen} aria-label={`Open ${cam.name}`}>
        <div className="tile-media">
          <LiveViewer cam={cam} view={lowBw} showOsd={false} onMotion={setMotion} />
          <div className="tile-badges">{statusBits}</div>
        </div>
        <div className="tile-bar">
          <div className="tile-bar-l">
            <span className="tile-name">{cam.name}</span>
            <span className="tile-id mono">{cam.id}</span>
          </div>
          <StatusPill status={cam.status} fps={cam.fps} size="sm" />
        </div>
      </button>
    );
  }

  if (layout === "data") {
    return (
      <button className="tile tile-data" onClick={onOpen} aria-label={`Open ${cam.name}`}>
        <div className="tile-media">
          <LiveViewer cam={cam} view={lowBw} showOsd={false} onMotion={setMotion} />
          <div className="tile-badges">{statusBits}</div>
          <div className="tile-name-ov">{cam.name}</div>
        </div>
        <div className="tile-stats">
          <div className="stat"><span className="stat-k">Status</span><StatusPill status={cam.status} size="sm" /></div>
          <div className="stat"><span className="stat-k">FPS</span><span className="stat-v mono">{cam.status === "offline" ? "—" : cam.fps.toFixed(1)}</span></div>
          <div className="stat"><span className="stat-k">Res</span><span className="stat-v mono">{cam.width}×{cam.height}</span></div>
          <div className="stat"><span className="stat-k">Backend</span><span className="stat-v mono">{cam.backend}</span></div>
        </div>
      </button>
    );
  }

  // cinematic (default) — overlay caption on the feed
  return (
    <button className="tile tile-cinematic" onClick={onOpen} aria-label={`Open ${cam.name}`}>
      <div className="tile-media">
        <LiveViewer cam={cam} view={lowBw} showOsd={false} onMotion={setMotion} />
        <div className="tile-grad" />
        <div className="tile-badges">{statusBits}</div>
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

Object.assign(window, { LiveViewer, CameraTile, useDebounced });
