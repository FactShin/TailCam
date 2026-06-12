import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useCameras, useSystem, useUpdate } from "../api/hooks";
import { BootOverlay } from "../components/BootOverlay";
import { CommandPalette } from "../components/CommandPalette";
import { VideoWall } from "../components/VideoWall";
import { fmtBytes } from "../lib/format";
import {
  IconChevL,
  IconChevR,
  IconFilm,
  IconGrid,
  IconMotion,
  IconSearch,
  IconSettings,
  IconWall,
  Logo,
  type IconProps,
} from "../icons";

const NAV: { to: string; label: string; icon: (p: IconProps) => JSX.Element; key: string }[] = [
  { to: "/", label: "Cameras", icon: IconGrid, key: "1" },
  { to: "/gallery", label: "Gallery", icon: IconFilm, key: "2" },
  { to: "/events", label: "Events", icon: IconMotion, key: "3" },
  { to: "/settings", label: "Settings", icon: IconSettings, key: "4" },
];

const isActive = (path: string, to: string) =>
  to === "/" ? path === "/" || path.startsWith("/camera/") : path.startsWith(to);

/** Dispatch from anywhere (dashboard button, palette) to open the video wall. */
export const openVideoWall = () => window.dispatchEvent(new CustomEvent("tailcam:wall"));

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const p = (n: number) => String(n).padStart(2, "0");
  return <>{`${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`}</>;
}

function TopTelemetry() {
  const navigate = useNavigate();
  const sys = useSystem().data;
  const cameras = useCameras().data ?? [];
  const recCount = cameras.filter((c) => c.recording).length;
  return (
    <div className="tele">
      {recCount > 0 && (
        <>
          <div className="tele-item tele-hide-sm">
            <span className="microlabel">Rec</span>
            <span className="v" style={{ color: "var(--err)" }}>
              <span className="rec-dot" />{recCount}
            </span>
          </div>
          <span className="tele-div tele-hide-sm" />
        </>
      )}
      {sys && (
        <>
          <button className="tele-item tele-hide-sm" onClick={() => navigate("/settings")} title="System & access">
            <span className="microlabel">Tailnet</span>
            <span className="v">
              <span className={`led ${sys.tailscale_running ? "ok" : "err"}`} />
              {sys.host}
            </span>
          </button>
          <span className="tele-div tele-hide-sm" />
          <div className="tele-item tele-hide-sm">
            <span className="microlabel">Storage</span>
            <span className="v">{fmtBytes(sys.media_bytes)}</span>
          </div>
          <span className="tele-div tele-hide-sm" />
        </>
      )}
      <div className="tele-item tele-clock">
        <span className="microlabel">Local</span>
        <span className="v"><Clock /></span>
      </div>
    </div>
  );
}

function UpdateBanner() {
  const upd = useUpdate().data;
  if (!upd?.available) return null;
  return (
    <div className="update-banner" role="status">
      Update available: {upd.current} → {upd.latest}. Run <code className="mono">tailcam update</code> on this device.
    </div>
  );
}

function ConnectionBanner() {
  const q = useCameras();
  if (!q.isError) return null;
  return (
    <div className="conn-banner" role="status">
      <span className="conn-dot" /> Connection lost — reconnecting…
    </div>
  );
}

function Brand({ onClick, mobile = false }: { onClick: () => void; mobile?: boolean }) {
  return (
    <button className={`brand ${mobile ? "brand-mobile" : ""}`} onClick={onClick} aria-label="TailCam home">
      <Logo size={mobile ? 24 : 30} />
      <span className="brand-text">
        <span className="brand-name">Tail<b>Cam</b></span>
        {!mobile && <span className="brand-sub">Tailnet Camera Grid</span>}
      </span>
    </button>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const path = useLocation().pathname;
  const sys = useSystem().data;
  const [palOpen, setPalOpen] = useState(false);
  const [wallOpen, setWallOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("tailcam.nav") === "collapsed"; } catch { return false; }
  });
  const toggleNav = () => {
    setCollapsed((c) => {
      try { localStorage.setItem("tailcam.nav", c ? "expanded" : "collapsed"); } catch { /* ignore */ }
      return !c;
    });
  };

  // Open the wall from anywhere (dashboard button, command palette, "w").
  useEffect(() => {
    const open = () => setWallOpen(true);
    window.addEventListener("tailcam:wall", open);
    return () => window.removeEventListener("tailcam:wall", open);
  }, []);

  // Global keyboard shortcuts.
  const onKey = useCallback(
    (e: KeyboardEvent) => {
      const tag = ((e.target as HTMLElement)?.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || (e.target as HTMLElement)?.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalOpen((o) => !o);
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const nav = NAV.find((n) => n.key === e.key);
      if (nav) navigate(nav.to);
      if (e.key.toLowerCase() === "w") setWallOpen(true);
    },
    [navigate],
  );
  useEffect(() => {
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onKey]);

  return (
    // .app-frame is the named CSS container ("appframe") that every desktop
    // @container rule (side rail, activity feed, grid columns) is gated on.
    <div className="app-frame">
      <div className="shell">
      <aside className={`sidebar ${collapsed ? "is-collapsed" : ""}`}>
        <Brand onClick={() => navigate("/")} />
        <nav className="side-nav">
          {NAV.map((n) => {
            const Ic = n.icon;
            const active = isActive(path, n.to);
            return (
              <button
                key={n.to}
                className={`side-link ${active ? "is-on" : ""}`}
                onClick={() => navigate(n.to)}
                aria-current={active ? "page" : undefined}
                title={`${n.label} (${n.key})`}
              >
                <Ic size={19} /><span>{n.label}</span><span className="navkey">{n.key}</span>
              </button>
            );
          })}
        </nav>
        <div className="side-mid">
          <button className="side-link" onClick={() => setWallOpen(true)} title="Video wall (W)">
            <IconWall size={19} /><span>Video wall</span><span className="navkey">W</span>
          </button>
        </div>
        <button
          className="collapse-btn"
          onClick={toggleNav}
          title={collapsed ? "Expand navigation" : "Collapse navigation"}
          aria-expanded={!collapsed}
        >
          {collapsed ? <IconChevR size={16} /> : <IconChevL size={16} />}<span>Collapse</span>
        </button>
        <div className="side-foot">
          <div className="side-foot-row">
            <span className={`led ${sys?.tailscale_running ? "ok" : "err"}`} />
            <span>tailnet</span>
            <span className="grow" />
            <span className="val">{sys?.host ?? "—"}</span>
          </div>
          <div className="side-foot-row">
            <span>TailCam</span>
            <span className="grow" />
            <span className="val">v{sys?.version ?? "—"}</span>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <Brand onClick={() => navigate("/")} mobile />
          <button className="palette-btn" onClick={() => setPalOpen(true)} aria-label="Open command palette">
            <IconSearch size={13} />
            <span className="palette-btn-label">Search</span>
            <span className="navkey">⌘K</span>
          </button>
          <span className="topbar-spacer" />
          <TopTelemetry />
        </header>
        <ConnectionBanner />
        <UpdateBanner />
        <main className="content">{children}</main>
      </div>

      <nav className="tabbar" aria-label="Primary">
        {NAV.map((n) => {
          const Ic = n.icon;
          const active = isActive(path, n.to);
          return (
            <button key={n.to} className={`tab ${active ? "is-on" : ""}`} onClick={() => navigate(n.to)} aria-current={active ? "page" : undefined}>
              <Ic size={20} /><span>{n.label}</span>
            </button>
          );
        })}
      </nav>

        <CommandPalette open={palOpen} onClose={() => setPalOpen(false)} onOpenWall={() => setWallOpen(true)} />
        {wallOpen && <VideoWall onClose={() => setWallOpen(false)} />}
        <BootOverlay />
      </div>
    </div>
  );
}
