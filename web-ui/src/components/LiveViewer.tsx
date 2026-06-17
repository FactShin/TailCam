import { useCallback, useEffect, useRef, useState } from "react";

import { snapshotUrl, streamUrl } from "../api/client";
import { useDetections, usePageVisible } from "../api/hooks";
import { IconZoom } from "../icons";
import { fmtDur, osdStamp } from "../lib/format";
import type { CameraInfo, ViewParams } from "../types";
import { Spinner } from "./ui";

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

// The on-screen rect of a frame displayed in a container under object-fit
// cover/contain — so normalized detection boxes land on the right pixels even
// with cropping (cover) or letterboxing (contain).
function displayedRect(
  cw: number,
  ch: number,
  iw: number,
  ih: number,
  fit: "cover" | "contain",
): { x: number; y: number; w: number; h: number } {
  if (cw <= 0 || ch <= 0 || iw <= 0 || ih <= 0) return { x: 0, y: 0, w: cw, h: ch };
  const scale = fit === "cover" ? Math.max(cw / iw, ch / ih) : Math.min(cw / iw, ch / ih);
  const w = iw * scale;
  const h = ih * scale;
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}

// WebKit (Safari on iOS *and* macOS) fails to render long-running
// multipart/x-mixed-replace MJPEG streams, especially over HTTP/2 (the
// Tailscale serve path). Fall back to polling snapshot.jpg there — lower fps,
// but it renders everywhere. We also auto-switch ANY browser to polling after
// repeated stream errors, so a renderer that can't do MJPEG never gets stuck.
const _ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
const _isSafari = /^((?!chrome|chromium|crios|android|fxios|edg).)*safari/i.test(_ua);
const IS_WEBKIT =
  /iPad|iPhone|iPod/.test(_ua) ||
  (_ua.includes("Mac") && navigator.maxTouchPoints > 1) ||
  _isSafari;
const SNAPSHOT_POLL_MAX_FPS = 3;
const MJPEG_ERRORS_BEFORE_FALLBACK = 2;

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
  // Overlay live object-detection boxes from the active detection model.
  detect?: boolean;
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
  detect = false,
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

  // Live object detection: overlay boxes only when not zoomed (the overlay maths
  // assume the whole frame is displayed). The query is gated so it idles when the
  // viewer is offscreen or no detection model is active.
  const detectOn = detect && shouldStream && view.zoom <= 1.02;
  const detection = useDetections(cam.proxy_prefix, cam.id, detectOn);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !detect || !("ResizeObserver" in window)) return;
    const ro = new ResizeObserver(([e]) =>
      setSize({ w: e.contentRect.width, h: e.contentRect.height }),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [detect]);
  const boxes = detectOn ? (detection.data?.boxes ?? []) : [];

  // Snapshot-polling mode: WebKit can't render MJPEG, and any browser that
  // errors on the stream repeatedly is switched over automatically.
  const [mjpegErrors, setMjpegErrors] = useState(0);
  const usePolling = IS_WEBKIT || mjpegErrors >= MJPEG_ERRORS_BEFORE_FALLBACK;

  const [pollTick, setPollTick] = useState(0);
  useEffect(() => {
    if (!usePolling || !shouldStream) return;
    const fps = Math.max(0.5, Math.min(debouncedView.fps, SNAPSHOT_POLL_MAX_FPS));
    const t = setInterval(() => setPollTick(Date.now()), 1000 / fps);
    setPollTick(Date.now());
    return () => clearInterval(t);
  }, [usePolling, shouldStream, debouncedView.fps]);

  let src: string | undefined;
  if (shouldStream) {
    if (usePolling) {
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
    // Count MJPEG errors → after a couple, this browser can't render the stream
    // (e.g. Safari), so switch to snapshot polling instead of looping forever.
    if (!usePolling) setMjpegErrors((n) => n + 1);
    setReconnecting(true);
    if (retryTimer.current) clearTimeout(retryTimer.current);
    retryTimer.current = setTimeout(() => {
      setNonce(Date.now());
      setBackoff((b) => Math.min(30, b * 1.6));
    }, backoff * 1000);
  }, [backoff, cam.status, usePolling]);

  const onLoad = useCallback(() => {
    setReconnecting(false);
    setBackoff(2);
    if (usePolling) setMjpegErrors(0);  // polling works; stop counting
  }, [usePolling]);

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

      {detectOn && boxes.length > 0 && (() => {
        const r = displayedRect(size.w, size.h, cam.width, cam.height, fit);
        return (
          <div className="det-layer">
            {boxes.map((b, i) => (
              <div
                key={i}
                className="det-box"
                style={{
                  left: r.x + (b.cx - b.w / 2) * r.w,
                  top: r.y + (b.cy - b.h / 2) * r.h,
                  width: b.w * r.w,
                  height: b.h * r.h,
                }}
              >
                <span className="det-tag">
                  {b.label} {Math.round(b.confidence * 100)}%
                </span>
              </div>
            ))}
          </div>
        );
      })()}

      {detect && (
        <div className="det-status mono">
          {detection.data && !detection.data.detector_active
            ? "no detection model active"
            : detectOn
              ? `detecting · ${boxes.length}`
              : "detect paused (zoomed)"}
        </div>
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
