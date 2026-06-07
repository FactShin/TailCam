import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useCameras, useSystem } from "../api/hooks";
import { fmtBytes } from "../lib/format";
import {
  IconFilm,
  IconGrid,
  IconMotion,
  IconSettings,
  IconWifi,
  IconWifiOff,
  Logo,
  type IconProps,
} from "../icons";

const NAV: { to: string; label: string; icon: (p: IconProps) => JSX.Element }[] = [
  { to: "/", label: "Cameras", icon: IconGrid },
  { to: "/gallery", label: "Gallery", icon: IconFilm },
  { to: "/events", label: "Events", icon: IconMotion },
  { to: "/settings", label: "Settings", icon: IconSettings },
];

const isActive = (path: string, to: string) =>
  to === "/" ? path === "/" || path.startsWith("/camera/") : path.startsWith(to);

function SystemChip({ onClick }: { onClick: () => void }) {
  const sys = useSystem().data;
  if (!sys) return null;
  const ok = sys.tailscale_running;
  return (
    <button className="syschip" onClick={onClick} title="System & access">
      <span className={`syschip-net ${ok ? "ok" : "warn"}`}>{ok ? <IconWifi size={15} /> : <IconWifiOff size={15} />}</span>
      <span className="syschip-url mono">{sys.access_url.replace(/^https?:\/\//, "")}</span>
      <span className="syschip-div" />
      <span className="syschip-stor mono">{fmtBytes(sys.media_bytes)}</span>
    </button>
  );
}

function ConnectionBanner() {
  // The cameras query polls every 2.5s; if it errors the server/Tailscale link
  // is down. Show a thin banner (and only once we've lost a working connection).
  const q = useCameras();
  if (!q.isError) return null;
  return (
    <div className="conn-banner" role="status">
      <span className="conn-dot" /> Connection lost — reconnecting…
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const path = useLocation().pathname;
  return (
    <div className="shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => navigate("/")} aria-label="AnyCam home">
          <Logo size={30} />
          <span className="brand-name">AnyCam</span>
        </button>
        <nav className="side-nav">
          {NAV.map((n) => {
            const Ic = n.icon;
            const active = isActive(path, n.to);
            return (
              <button key={n.to} className={`side-link ${active ? "is-on" : ""}`} onClick={() => navigate(n.to)} aria-current={active ? "page" : undefined}>
                <Ic size={20} /><span>{n.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="side-foot mono">Tailscale</div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="brand brand-mobile" onClick={() => navigate("/")} aria-label="AnyCam home">
            <Logo size={26} /><span className="brand-name">AnyCam</span>
          </button>
          <div className="topbar-right">
            <SystemChip onClick={() => navigate("/settings")} />
          </div>
        </header>
        <ConnectionBanner />
        <main className="content">{children}</main>
      </div>

      <nav className="tabbar" aria-label="Primary">
        {NAV.map((n) => {
          const Ic = n.icon;
          const active = isActive(path, n.to);
          return (
            <button key={n.to} className={`tab ${active ? "is-on" : ""}`} onClick={() => navigate(n.to)} aria-current={active ? "page" : undefined}>
              <Ic size={22} /><span>{n.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
