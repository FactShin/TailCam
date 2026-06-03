import type { ButtonHTMLAttributes, ReactNode } from "react";
import { useEffect } from "react";

import { IconClose, IconGlobe, IconPhone, type IconProps } from "../icons";
import type { CameraStatus } from "../types";

export function StatusPill({
  status,
  fps,
  size = "md",
}: {
  status: CameraStatus;
  fps?: number;
  size?: "sm" | "md";
}) {
  const map: Record<CameraStatus, { c: string; label: string }> = {
    online: { c: "var(--ok)", label: "Online" },
    degraded: { c: "var(--warn)", label: "Degraded" },
    offline: { c: "var(--err)", label: "Offline" },
  };
  const s = map[status] ?? map.offline;
  return (
    <span className={`pill pill-${size}`} role="status">
      <span
        className="pill-dot"
        style={{ background: s.c, boxShadow: status === "online" ? `0 0 0 3px ${s.c}22` : "none" }}
      />
      <span style={{ color: s.c }}>{s.label}</span>
      {fps != null && status !== "offline" && <span className="pill-fps mono">{fps.toFixed(1)} fps</span>}
    </span>
  );
}

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "ghost" | "primary" | "outline" | "danger";
  size?: "sm" | "md";
  icon?: ReactNode;
}
export function Button({ children, variant = "ghost", size = "md", icon, className = "", ...rest }: BtnProps) {
  return (
    <button className={`btn btn-${variant} btn-${size} ${className}`} {...rest}>
      {icon && <span className="btn-ic">{icon}</span>}
      {children && <span>{children}</span>}
    </button>
  );
}

type Opt = string | { value: string | number; label: string };
export function Segmented({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: Opt[];
  value: string | number;
  onChange: (v: never) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => {
        const val = typeof o === "object" ? o.value : o;
        const label = typeof o === "object" ? o.label : o;
        const active = val === value;
        return (
          <button
            key={String(val)}
            role="radio"
            aria-checked={active}
            className={`seg-opt ${active ? "is-on" : ""}`}
            onClick={() => onChange(val as never)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function ControlSlider({
  label,
  icon,
  value,
  min,
  max,
  step = 1,
  unit = "",
  onChange,
  onCommit,
  format,
  disabled,
}: {
  label: string;
  icon?: ReactNode;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (v: number) => void;
  onCommit?: () => void;
  format?: (v: number) => string;
  disabled?: boolean;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  const display = format ? format(value) : `${value}${unit}`;
  return (
    <div className={`slider ${disabled ? "is-disabled" : ""}`}>
      <div className="slider-head">
        <span className="slider-label">{icon && <span className="slider-ic">{icon}</span>}{label}</span>
        <span className="slider-val mono">{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        onMouseUp={() => onCommit?.()}
        onTouchEnd={() => onCommit?.()}
        onKeyUp={() => onCommit?.()}
        style={{ ["--pct" as string]: pct + "%" }}
      />
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`toggle ${checked ? "is-on" : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-knob" />
    </button>
  );
}

export function Spinner({ size = 18 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-label="loading" />;
}

export function BottomSheet({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
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

export function ScopeBadge({ scope }: { scope: "global" | "local" }) {
  const iconProps: IconProps = { size: 13 };
  if (scope === "global")
    return <span className="scope scope-global"><IconGlobe {...iconProps} /> Affects everyone</span>;
  return <span className="scope scope-local"><IconPhone {...iconProps} /> This device only</span>;
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Delete",
  danger = true,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
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

export { IconClose };
