// app.jsx — router, AppShell (responsive nav + bottom tab bar), Tweaks, mount.

const { useState: useA, useEffect: useEA, useRef: useRA, useCallback: useCA, createContext: createCtxA, useContext: useCtxA } = React;

// ---- tiny hash router (ids may contain slashes) ----
const RouterCtx = createCtxA(null);
function parseHash() {
  let h = window.location.hash.replace(/^#/, "");
  if (!h) h = "/";
  return h;
}
function useRouterState() {
  const [path, setPath] = useA(parseHash());
  useEA(() => {
    const on = () => setPath(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const navigate = useCA((to) => { window.location.hash = to; }, []);
  return { path, navigate };
}
window.useNavigate = () => useCtxA(RouterCtx).navigate;

// ---- shell width context (drives auto detail layout + container fallbacks) ----
const ShellCtx = createCtxA({ wide: true, width: 1200 });
const useShell = () => useCtxA(ShellCtx);

// ---- System chip ----
function SystemChip({ onClick }) {
  const sys = useSystem();
  const ok = sys.tailscale_running;
  return (
    <button className="syschip" onClick={onClick} title="System & access">
      <span className={`syschip-net ${ok ? "ok" : "warn"}`}>{ok ? <IconWifi size={15} /> : <IconWifiOff size={15} />}</span>
      <span className="syschip-url mono">{sys.access_url.replace("http://", "")}</span>
      <span className="syschip-div" />
      <span className="syschip-stor mono"><IconHdd size={13} /> {fmtBytes(sys.media_bytes)}</span>
    </button>
  );
}

const NAV = [
  { to: "/", label: "Cameras", icon: IconGrid },
  { to: "/gallery", label: "Gallery", icon: IconFilm },
  { to: "/events", label: "Events", icon: IconMotion },
  { to: "/settings", label: "Settings", icon: IconSettings },
];
const isActive = (path, to) => to === "/" ? (path === "/" || path.startsWith("/camera/")) : path.startsWith(to);

function AppShell({ path, children }) {
  const nav = window.useNavigate();
  return (
    <div className="shell">
      {/* desktop sidebar */}
      <aside className="sidebar">
        <button className="brand" onClick={() => nav("/")} aria-label="AnyCam home">
          <Logo size={30} />
          <span className="brand-name">AnyCam</span>
        </button>
        <nav className="side-nav">
          {NAV.map((n) => {
            const Ic = n.icon, active = isActive(path, n.to);
            return (
              <button key={n.to} className={`side-link ${active ? "is-on" : ""}`} onClick={() => nav(n.to)} aria-current={active ? "page" : undefined}>
                <Ic size={20} /><span>{n.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="side-foot mono">v1.4.2 · Tailscale</div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="brand brand-mobile" onClick={() => nav("/")} aria-label="AnyCam home">
            <Logo size={26} /><span className="brand-name">AnyCam</span>
          </button>
          <div className="topbar-right">
            <SystemChip onClick={() => nav("/settings")} />
          </div>
        </header>
        <main className="content">{children}</main>
      </div>

      {/* mobile bottom tab bar */}
      <nav className="tabbar" aria-label="Primary">
        {NAV.map((n) => {
          const Ic = n.icon, active = isActive(path, n.to);
          return (
            <button key={n.to} className={`tab ${active ? "is-on" : ""}`} onClick={() => nav(n.to)} aria-current={active ? "page" : undefined}>
              <Ic size={22} /><span>{n.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}

// ---- Tweaks ----
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "tileLayout": "cinematic",
  "detailLayout": "auto",
  "accent": "#4f8cff",
  "density": "comfortable",
  "device": "desktop"
}/*EDITMODE-END*/;

const DEVICE_W = { desktop: null, tablet: 834, phone: 390 };

function Root() {
  const { path, navigate } = useRouterState();
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const frameRef = useRA(null);
  const [shellW, setShellW] = useA(1200);

  // accent + density on root
  useEA(() => {
    document.documentElement.style.setProperty("--accent", t.accent);
    document.documentElement.dataset.density = t.density;
  }, [t.accent, t.density]);

  // observe shell width for auto layout
  useEA(() => {
    const el = frameRef.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setShellW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [t.device]);

  // route
  let screen;
  if (path.startsWith("/camera/")) {
    const id = path.slice("/camera/".length);
    screen = <CameraDetail id={id} detailLayout={t.detailLayout === "auto" ? (shellW >= 1000 ? "side" : "sheet") : t.detailLayout} />;
  } else if (path.startsWith("/gallery")) screen = <Gallery />;
  else if (path.startsWith("/events")) screen = <Events />;
  else if (path.startsWith("/settings")) screen = <Settings />;
  else screen = <Dashboard tile={t.tileLayout} />;

  const dw = DEVICE_W[t.device];
  const frameStyle = dw ? { width: dw, maxWidth: "100%" } : {};

  return (
    <RouterCtx.Provider value={{ path, navigate }}>
      <ShellCtx.Provider value={{ wide: shellW >= 1000, width: shellW }}>
        <div className={`device-stage device-${t.device}`}>
          <div className={`app-frame ${t.device !== "desktop" ? "is-device" : ""}`} ref={frameRef} style={frameStyle} data-w={shellW >= 1000 ? "wide" : "narrow"}>
            <ToastProvider>
              <AppShell path={path}>{screen}</AppShell>
            </ToastProvider>
          </div>
        </div>

        <TweaksPanel title="Tweaks">
          <TweakSection label="Dashboard tiles" />
          <TweakRadio label="Tile layout" value={t.tileLayout} options={["cinematic", "compact", "data"]} onChange={(v) => setTweak("tileLayout", v)} />
          <TweakSection label="Camera detail" />
          <TweakRadio label="Controls" value={t.detailLayout} options={[{ value: "auto", label: "Auto" }, { value: "side", label: "Side" }, { value: "sheet", label: "Sheet" }]} onChange={(v) => setTweak("detailLayout", v)} />
          <TweakSection label="Appearance" />
          <TweakColor label="Accent" value={t.accent} options={["#4f8cff", "#3ecf8e", "#f5a623", "#a07bff", "#ff6b9d"]} onChange={(v) => setTweak("accent", v)} />
          <TweakRadio label="Density" value={t.density} options={["comfortable", "compact"]} onChange={(v) => setTweak("density", v)} />
          <TweakSection label="Preview device" />
          <TweakRadio label="Viewport" value={t.device} options={[{ value: "desktop", label: "Desktop" }, { value: "tablet", label: "Tablet" }, { value: "phone", label: "Phone" }]} onChange={(v) => setTweak("device", v)} />
        </TweaksPanel>
      </ShellCtx.Provider>
    </RouterCtx.Provider>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<Root />);
