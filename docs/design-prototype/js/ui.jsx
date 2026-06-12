// ui.jsx — primitives: formatters, StatusPill, ControlSlider, Segmented, Button,
// BottomSheet, Toast system, Spinner, Confirm dialog.

const { useState: useStateUI, useEffect: useEffectUI, useRef: useRefUI, useCallback: useCallbackUI, createContext, useContext } = React;

// ---- formatters ----
function fmtBytes(b) {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(b) / Math.log(1024));
  return `${(b / Math.pow(1024, i)).toFixed(i ? 1 : 0)} ${u[i]}`;
}
function fmtClock(ts) {
  const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function fmtDateTime(ts) {
  const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtAgo(ts) {
  const s = Math.max(0, Math.floor(NOW() - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
function fmtDur(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}

// ---- StatusPill ----
function StatusPill({ status, fps, size = "md" }) {
  const map = {
    online: { c: "var(--ok)", label: "Online" },
    degraded: { c: "var(--warn)", label: "Degraded" },
    offline: { c: "var(--err)", label: "Offline" },
  };
  const s = map[status] || map.offline;
  return (
    <span className={`pill pill-${size}`} role="status" aria-label={`${s.label}${fps != null && status !== "offline" ? `, ${fps.toFixed(1)} fps` : ""}`}>
      <span className="pill-dot" style={{ background: s.c, boxShadow: status === "online" ? `0 0 0 3px ${s.c}22` : "none" }} />
      <span style={{ color: s.c }}>{s.label}</span>
      {fps != null && status !== "offline" && <span className="pill-fps mono">{fps.toFixed(1)} fps</span>}
    </span>
  );
}

// ---- Button ----
function Button({ children, variant = "ghost", size = "md", icon, className = "", ...rest }) {
  return (
    <button className={`btn btn-${variant} btn-${size} ${className}`} {...rest}>
      {icon && <span className="btn-ic">{icon}</span>}
      {children && <span>{children}</span>}
    </button>
  );
}

// ---- Segmented control ----
function Segmented({ options, value, onChange, ariaLabel }) {
  return (
    <div className="seg" role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => {
        const val = typeof o === "object" ? o.value : o;
        const label = typeof o === "object" ? o.label : o;
        const active = val === value;
        return (
          <button key={String(val)} role="radio" aria-checked={active} className={`seg-opt ${active ? "is-on" : ""}`} onClick={() => onChange(val)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

// ---- ControlSlider ----
function ControlSlider({ label, icon, value, min, max, step = 1, unit = "", onChange, onCommit, format, disabled }) {
  const pct = ((value - min) / (max - min)) * 100;
  const display = format ? format(value) : `${value}${unit}`;
  return (
    <div className={`slider ${disabled ? "is-disabled" : ""}`}>
      <div className="slider-head">
        <span className="slider-label">{icon && <span className="slider-ic">{icon}</span>}{label}</span>
        <span className="slider-val mono">{display}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        onMouseUp={() => onCommit && onCommit()}
        onTouchEnd={() => onCommit && onCommit()}
        onKeyUp={() => onCommit && onCommit()}
        style={{ "--pct": pct + "%" }}
      />
    </div>
  );
}

// ---- Toggle ----
function Toggle({ checked, onChange, label, disabled }) {
  return (
    <button role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      className={`toggle ${checked ? "is-on" : ""}`} onClick={() => onChange(!checked)}>
      <span className="toggle-knob" />
    </button>
  );
}

// ---- Spinner ----
function Spinner({ size = 18 }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-label="loading" />;
}

// ---- BottomSheet (mobile) — drag handle, backdrop, esc, reduced-motion safe ----
function BottomSheet({ open, onClose, title, children, footer }) {
  useEffectUI(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <div className={`sheet-root ${open ? "is-open" : ""}`} aria-hidden={!open}>
      <div className="sheet-backdrop" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet-grab" onClick={onClose}><span /></div>
        {title && <div className="sheet-title">{title}</div>}
        <div className="sheet-body">{children}</div>
        {footer && <div className="sheet-footer">{footer}</div>}
      </div>
    </div>
  );
}

// ---- Toast system ----
const ToastCtx = createContext(null);
let _toastId = 1;
function ToastProvider({ children }) {
  const [toasts, setToasts] = useStateUI([]);
  const remove = useCallbackUI((id) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallbackUI((msg, opts = {}) => {
    const id = _toastId++;
    setToasts((t) => [...t, { id, msg, kind: opts.kind || "info", action: opts.action }]);
    if (opts.kind !== "loading") setTimeout(() => remove(id), opts.duration || 3200);
    return id;
  }, [remove]);
  const toast = {
    info: (m, o) => push(m, { ...o, kind: "info" }),
    ok: (m, o) => push(m, { ...o, kind: "ok" }),
    err: (m, o) => push(m, { ...o, kind: "err" }),
    remove,
  };
  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div className="toast-wrap" role="region" aria-label="Notifications">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} role="status">
            <span className="toast-dot" />
            <span className="toast-msg">{t.msg}</span>
            {t.action && <button className="toast-action" onClick={() => { t.action.fn(); remove(t.id); }}>{t.action.label}</button>}
            <button className="toast-x" aria-label="Dismiss" onClick={() => remove(t.id)}><IconClose size={14} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
const useToast = () => useContext(ToastCtx);

// ---- Confirm dialog ----
function ConfirmDialog({ open, title, body, confirmLabel = "Delete", danger = true, onConfirm, onCancel }) {
  useEffectUI(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && onCancel();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="modal-root" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-backdrop" onClick={onCancel} />
      <div className="modal-card">
        <div className="modal-title">{title}</div>
        <div className="modal-body">{body}</div>
        <div className="modal-actions">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

// ---- Section header with global/local scope badge ----
function ScopeBadge({ scope }) {
  // scope: "global" (affects everyone) | "local" (this device only)
  if (scope === "global")
    return <span className="scope scope-global"><IconGlobe size={13} /> Affects everyone</span>;
  return <span className="scope scope-local"><IconPhone size={13} /> This device only</span>;
}

Object.assign(window, {
  fmtBytes, fmtClock, fmtDateTime, fmtAgo, fmtDur,
  StatusPill, Button, Segmented, ControlSlider, Toggle, Spinner, BottomSheet,
  ToastProvider, useToast, ConfirmDialog, ScopeBadge,
});
