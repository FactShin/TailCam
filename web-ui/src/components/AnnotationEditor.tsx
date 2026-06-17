// Bounding-box annotation editor for detection datasets. Drag on the image to
// draw a box, give it a class, save the whole set. Coordinates are kept
// normalized (0..1, center + size) so they're resolution-independent — the
// same layout the backend stores and YOLO training consumes.

import { useEffect, useMemo, useRef, useState } from "react";

import { sampleImageUrl } from "../api/client";
import { useAnnotations, useSetAnnotations } from "../api/hooks";
import { useToast } from "./toast";
import { Button } from "./ui";
import { IconTrash } from "../icons";
import type { AnnotationBox } from "../types";

interface DraftBox extends AnnotationBox {
  key: string; // local id so React keys are stable while editing
}

let _seq = 0;
const newKey = () => `b${_seq++}`;

export function AnnotationEditor({
  sampleId,
  classes,
  onClose,
}: {
  sampleId: number;
  classes: string[];
  onClose: () => void;
}) {
  const loaded = useAnnotations(sampleId).data;
  const save = useSetAnnotations();
  const toast = useToast();
  const stageRef = useRef<HTMLDivElement | null>(null);

  const [boxes, setBoxes] = useState<DraftBox[]>([]);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [activeLabel, setActiveLabel] = useState(classes[0] ?? "object");

  // Seed from the server once it arrives.
  useEffect(() => {
    if (loaded) setBoxes(loaded.boxes.map((b) => ({ ...b, key: newKey() })));
  }, [loaded]);

  // Class suggestions: configured classes plus any already drawn here.
  const labelOptions = useMemo(() => {
    const set = new Set<string>(classes);
    boxes.forEach((b) => set.add(b.label));
    return [...set].filter(Boolean);
  }, [classes, boxes]);

  const relToStage = (e: { clientX: number; clientY: number }) => {
    const el = stageRef.current;
    if (!el) return { x: 0, y: 0 };
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const { x, y } = relToStage(e);
    setDrag({ x0: x, y0: y, x1: x, y1: y });
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const { x, y } = relToStage(e);
    setDrag({ ...drag, x1: x, y1: y });
  };
  const onPointerUp = () => {
    if (!drag) return;
    const w = Math.abs(drag.x1 - drag.x0);
    const h = Math.abs(drag.y1 - drag.y0);
    if (w > 0.01 && h > 0.01) {
      const cx = (drag.x0 + drag.x1) / 2;
      const cy = (drag.y0 + drag.y1) / 2;
      setBoxes((prev) => [...prev, { key: newKey(), label: activeLabel || "object", cx, cy, w, h }]);
    }
    setDrag(null);
  };

  const setBoxLabel = (key: string, label: string) =>
    setBoxes((prev) => prev.map((b) => (b.key === key ? { ...b, label } : b)));
  const removeBox = (key: string) => setBoxes((prev) => prev.filter((b) => b.key !== key));

  const onSave = async () => {
    const payload: AnnotationBox[] = boxes
      .filter((b) => b.label.trim())
      .map(({ label, cx, cy, w, h }) => ({ label: label.trim(), cx, cy, w, h }));
    try {
      await save.mutateAsync({ sampleId, boxes: payload });
      toast.ok(`Saved ${payload.length} box${payload.length === 1 ? "" : "es"}`);
      onClose();
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Could not save boxes");
    }
  };

  const pct = (n: number) => `${(n * 100).toFixed(3)}%`;
  const dragRect = drag
    ? {
        left: pct(Math.min(drag.x0, drag.x1)),
        top: pct(Math.min(drag.y0, drag.y1)),
        width: pct(Math.abs(drag.x1 - drag.x0)),
        height: pct(Math.abs(drag.y1 - drag.y0)),
      }
    : null;

  return (
    <div className="anno-overlay" role="dialog" aria-label="Annotate sample" onClick={onClose}>
      <div className="anno-modal" onClick={(e) => e.stopPropagation()}>
        <div className="anno-head">
          <span className="panel-title" style={{ margin: 0 }}>
            Annotate · {boxes.length} box{boxes.length === 1 ? "" : "es"}
          </span>
          <span className="grow" />
          <label className="tl-field" style={{ minWidth: 160 }}>
            <span className="microlabel">New box class</span>
            <input
              className="tl-input"
              list="anno-classes"
              value={activeLabel}
              placeholder="person, dog, …"
              onChange={(e) => setActiveLabel(e.target.value)}
            />
            <datalist id="anno-classes">
              {labelOptions.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </label>
        </div>

        <div
          ref={stageRef}
          className="anno-stage"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        >
          <img src={sampleImageUrl(sampleId)} alt="sample" draggable={false} />
          {boxes.map((b) => (
            <div
              key={b.key}
              className="anno-box"
              style={{
                left: pct(b.cx - b.w / 2),
                top: pct(b.cy - b.h / 2),
                width: pct(b.w),
                height: pct(b.h),
              }}
            >
              <span className="anno-box-tag">{b.label || "—"}</span>
            </div>
          ))}
          {dragRect && <div className="anno-box is-draft" style={dragRect} />}
        </div>

        <p className="help-foot mono" style={{ margin: "8px 0 0" }}>
          Drag on the image to draw a box. Set each box's class below, then save.
        </p>

        {boxes.length > 0 && (
          <div className="anno-list">
            {boxes.map((b) => (
              <div key={b.key} className="anno-list-row">
                <input
                  className="tl-input"
                  list="anno-classes"
                  value={b.label}
                  onChange={(e) => setBoxLabel(b.key, e.target.value)}
                />
                <span className="mono anno-coords">
                  {(b.w * 100).toFixed(0)}×{(b.h * 100).toFixed(0)}%
                </span>
                <button className="sample-del" aria-label="Remove box" onClick={() => removeBox(b.key)}>
                  <IconTrash size={13} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="anno-actions">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={save.isPending} onClick={onSave}>
            Save boxes
          </Button>
        </div>
      </div>
    </div>
  );
}
