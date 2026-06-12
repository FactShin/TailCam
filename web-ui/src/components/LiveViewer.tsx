import { useCallback, useEffect, useRef, useState } from "react";

import { snapshotUrl, streamUrl } from "../api/client";
import { usePageVisible } from "../api/hooks";
import { IconZoom } from "../icons";
import { fmtDur, osdStamp } from "../lib/format";
import type { CameraInfo, ViewParams } from "../types";
import { Spinner } from "./ui";

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

// iOS WebKit (every iPhone/iPad browser) fails to render long-running
// multipart/x-mixed-replace MJPEG streams, especially over HTTP/2 (the
// Tailscale serve path). Fall back to polling snapshot.jpg there — lower fps,
// but it renders everywhere. iPadOS masquerades as macOS, hence the touch check.
const IS_IOS_WEBKIT =
  typeof navigator !== "undefined" &&
  (/iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.userAgent.includes("Mac") && navigator.maxTouchPoints > 1));
const SNAPSHOT_POLL_MAX_FPS = 3;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [JSON.stringify(value), ms]);
  return v;
}

interface Props {
  cam: CameraInfo;
  view: ViewParams;
  onView?: (v: ViewParams) => void;
  big?: boolean;
  interactive?: boolean;
  showOsd?: boolean;
  showUrl?: boolean;
  fit?: "cover" | "contain";
}

export function LiveViewer({
  cam,
  view,
  onView,
  big = false,
  interactive = false,
  showOsd = true,
  showUrl = false,
  fit = "cover",
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);
  const [reconnecting, setReconnecting] = useState(cam.status === "offline");
  const [backoff, setBackoff] = useState(2);
  const [nonce, setNonce] = useState(0);
  const [stamp, setStamp] = useState(osdStamp());
  const [recElapsed, setRecElapsed] = useState(0);
  const recStart = useRef<number | null>(null);
  const pageVisible = usePageVisible();
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debouncedView = useDebounced(view, 260);

  // Pause the stream when offscreen (low-bandwidth) unless this is the big view.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !("IntersectionObserver" in window)) {
      setInView(true);
      return;
    }
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { threshold: 0.05 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const active = (pageVisible && inView) || big;
  const shouldStream = active && cam.status !== "offline";

  // iOS compatibility mode: poll single JPEG snapshots instead of MJPEG.
  const [pollTick, setPollTick] = useState(0);
  useEffect(() => {
    if (!IS_IOS_WEBKIT || !shouldStream) return;
    const fps = Math.max(0.5, Math.min(debouncedView.fps, SNAPSHOT_POLL_MAX_FPS));
    const t = setInterval(() => setPollTick(Date.now()), 1000 / fps);
    setPollTick(Date.now());
    return () => clearInterval(t);
  }, [shouldStream, debouncedView.fps]);

  let src: string | undefined;
  if (shouldStream) {
    if (IS_IOS_WEBKIT) {
      src = `${snapshotUrl(cam.proxy_prefix, cam.id)}?_=${pollTick}`;
    } else {
      const base = streamUrl(cam.proxy_prefix, cam.id, debouncedView);
      src = nonce ? `${base}&_=${nonce}` : base;
    }
  }

  // REC timer
  useEffect(() => {
    if (cam.recording) {
      if (recStart.current == null) recStart.current = Date.now() / 1000;
    } else {
      recStart.current = null;
      setRecElapsed(0);
    }
  }, [cam.recording]);

  // OSD clock + rec elapsed
  useEffect(() => {
    const t = setInterval(() => {
      setStamp(osdStamp());
      if (cam.recording && recStart.current != null) setRecElapsed(Date.now() / 1000 - recStart.current);
    }, 1000);
    return () => clearInterval(t);
  }, [cam.recording]);

  // Offline state reflects camera status.
  useEffect(() => {
    setReconnecting(cam.status === "offline");
  }, [cam.status]);

  const onError = useCallback(() => {
    if (cam.status === "offline") return;
    setReconnecting(true);
    if (retryTimer.current) clearTimeout(retryTimer.current);
    retryTimer.current = setTimeout(() => {
      setNonce(Date.now());
      setBackoff((b) => Math.min(30, b * 1.6));
    }, backoff * 1000);
  }, [backoff, cam.status]);

  const onLoad = useCallback(() => {
    setReconnecting(false);
    setBackoff(2);
  }, []);

  useEffect(() => () => { if (retryTimer.current) clearTimeout(retryTimer.current); }, []);

  // ---- gestures (interactive detail view only) ----
  const g = useRef({ dragging: false, sx: 0, sy: 0, pPanX: 0.5, pPanY: 0.5, pinchD: 0, pZoom: 1 });
  const dist = (t: React.TouchList) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);

  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      if (!interactive || !onView) return;
      const nz = clamp(+(view.zoom * (e.deltaY < 0 ? 1.12 : 0.89)).toFixed(2), 1, 8);
      onView({ ...view, zoom: nz, panX: nz <= 1 ? 0.5 : view.panX, panY: nz <= 1 ? 0.5 : view.panY });
    },
    [interactive, view, onView],
  );
  const onTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (!interactive) return;
      const t = e.touches;
      if (t.length === 2) {
        g.current.pinchD = dist(t);
        g.current.pZoom = view.zoom;
      } else if (t.length === 1 && view.zoom > 1) {
        g.current.dragging = true;
        g.current.sx = t[0].clientX;
        g.current.sy = t[0].clientY;
        g.current.pPanX = view.panX;
        g.current.pPanY = view.panY;
      }
    },
    [interactive, view],
  );
  const onTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!interactive || !onView) return;
      const t = e.touches;
      if (t.length === 2 && g.current.pinchD) {
        const nz = clamp(+(g.current.pZoom * (dist(t) / g.current.pinchD)).toFixed(2), 1, 8);
        onView({ ...view, zoom: nz });
      } else if (g.current.dragging && t.length === 1) {
        const rect = wrapRef.current!.getBoundingClientRect();
        const dx = (t[0].clientX - g.current.sx) / rect.width;
        const dy = (t[0].clientY - g.current.sy) / rect.height;
        const k = 1 / view.zoom;
        onView({
          ...view,
          panX: clamp(g.current.pPanX - dx * k, 0, 1),
          panY: clamp(g.current.pPanY - dy * k, 0, 1),
        });
      }
    },
    [interactive, view, onView],
  );
  const onTouchEnd = useCallback(() => {
    g.current.dragging = false;
    g.current.pinchD = 0;
  }, []);
  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!interactive || view.zoom <= 1) return;
      g.current.dragging = true;
      g.current.sx = e.clientX;
      g.current.sy = e.clientY;
      g.current.pPanX = view.panX;
      g.current.pPanY = view.panY;
    },
    [interactive, view],
  );
  useEffect(() => {
    if (!interactive || !onView) return;
    const move = (e: MouseEvent) => {
      if (!g.current.dragging) return;
      const rect = wrapRef.current!.getBoundingClientRect();
      const dx = (e.clientX - g.current.sx) / rect.width;
      const dy = (e.clientY - g.current.sy) / rect.height;
      const k = 1 / view.zoom;
      onView({
        ...view,
        panX: clamp(g.current.pPanX - dx * k, 0, 1),
        panY: clamp(g.current.pPanY - dy * k, 0, 1),
      });
    };
    const up = () => { g.current.dragging = false; };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [interactive, view, onView]);

  return (
    <div
      ref={wrapRef}
      className={`viewer ${big ? "viewer-big" : ""} ${interactive ? "viewer-grab" : ""}`}
      onWheel={onWheel}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onMouseDown={onMouseDown}
      style={{ cursor: interactive && view.zoom > 1 ? "grab" : "default" }}
    >
      {src ? (
        <img
          className="viewer-canvas"
          style={{ objectFit: fit, width: "100%", height: "100%" }}
          src={src}
          alt={cam.name}
          draggable={false}
          onError={onError}
          onLoad={onLoad}
        />
      ) : (
        <div className="viewer-canvas viewer-paused" />
      )}

      {showOsd && cam.status !== "offline" && <div className="osd mono">{stamp}</div>}

      {cam.status !== "offline" && (
        <div className="viewer-tl">
          <span className="chip-live"><span className="live-dot" />LIVE</span>
          {cam.recording && <span className="chip-rec"><span className="rec-dot" />REC {fmtDur(recElapsed)}</span>}
        </div>
      )}

      {interactive && view.zoom > 1.02 && (
        <div className="viewer-zoomchip mono"><IconZoom size={13} /> {view.zoom.toFixed(1)}×</div>
      )}

      {reconnecting && (
        <div className="viewer-overlay">
          <div className="overlay-inner">
            <Spinner size={26} />
            <div className="overlay-title">{cam.status === "offline" ? "Offline" : "Reconnecting"}</div>
            <div className="overlay-sub mono">
              {cam.status === "offline" ? "camera not responding" : `retry in ${Math.round(backoff)}s`}
            </div>
          </div>
        </div>
      )}

      {showUrl && src && (
        <div className="viewer-url mono" title="The MJPEG <img> src — these params are per-tab only">
          GET {src}
        </div>
      )}
    </div>
  );
}
