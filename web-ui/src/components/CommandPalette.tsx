import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useCameras } from "../api/hooks";
import { IconCamera, IconFilm, IconGrid, IconMotion, IconSearch, IconSettings, IconTimelapse, IconWall } from "../icons";
import { cameraPath } from "../lib/nav";

interface PalItem {
  kind: "screen" | "camera" | "wall";
  id: string;
  label: string;
  icon: (p: { size?: number }) => JSX.Element;
  meta: string;
  to?: string;
}

export function CommandPalette({
  open,
  onClose,
  onOpenWall,
}: {
  open: boolean;
  onClose: () => void;
  onOpenWall: () => void;
}) {
  const navigate = useNavigate();
  const cameras = useCameras().data ?? [];
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const items = useMemo<PalItem[]>(() => {
    const screens: PalItem[] = [
      { kind: "screen", id: "/", label: "Cameras", icon: IconGrid, meta: "screen", to: "/" },
      { kind: "screen", id: "/gallery", label: "Gallery", icon: IconFilm, meta: "screen", to: "/gallery" },
      { kind: "screen", id: "/events", label: "Events", icon: IconMotion, meta: "screen", to: "/events" },
      { kind: "screen", id: "/timelapse", label: "Timelapse", icon: IconTimelapse, meta: "screen", to: "/timelapse" },
      { kind: "screen", id: "/settings", label: "Settings", icon: IconSettings, meta: "screen", to: "/settings" },
    ];
    const cams: PalItem[] = cameras.map((c) => ({
      kind: "camera",
      id: `${c.host}/${c.id}`,
      label: c.name,
      icon: IconCamera,
      meta: `${c.host} · ${c.status}`,
      to: cameraPath(c),
    }));
    const actions: PalItem[] = [{ kind: "wall", id: "wall", label: "Open video wall", icon: IconWall, meta: "W" }];
    const all = [...actions, ...cams, ...screens];
    if (!q.trim()) return all;
    const needle = q.toLowerCase();
    return all.filter((i) => i.label.toLowerCase().includes(needle) || i.meta.toLowerCase().includes(needle));
  }, [q, cameras]);

  useEffect(() => {
    if (open) {
      setQ("");
      setSel(0);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);
  useEffect(() => setSel(0), [q]);

  if (!open) return null;

  const run = (item: PalItem) => {
    onClose();
    if (item.kind === "wall") onOpenWall();
    else if (item.to) navigate(item.to);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    if (e.key === "Enter" && items[sel]) run(items[sel]);
    if (e.key === "Escape") onClose();
  };

  return (
    <div className="pal-root" role="dialog" aria-modal="true" aria-label="Command palette">
      <div className="pal-backdrop" onClick={onClose} />
      <div className="pal">
        <div className="pal-head">
          <IconSearch size={16} />
          <input
            ref={inputRef}
            className="pal-input"
            placeholder="Jump to a camera, screen, or action…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            aria-label="Command search"
          />
          <span className="navkey">ESC</span>
        </div>
        <div className="pal-list">
          {items.length === 0 && <div className="pal-sec"><span className="microlabel">No matches</span></div>}
          {items.map((item, i) => {
            const Ic = item.icon;
            return (
              <button
                key={item.kind + item.id}
                className={`pal-item ${i === sel ? "is-sel" : ""}`}
                onMouseEnter={() => setSel(i)}
                onClick={() => run(item)}
              >
                <Ic size={16} />
                <span className="grow">{item.label}</span>
                <span className="pal-meta">{item.meta}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
