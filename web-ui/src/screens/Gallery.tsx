import { useEffect, useRef, useState } from "react";

import { mediaFileUrl, mediaThumbUrl } from "../api/client";
import { useCameras, useDeleteMedia, useMedia } from "../api/hooks";
import { useToast } from "../components/toast";
import { Button, ConfirmDialog, Segmented, Spinner } from "../components/ui";
import {
  IconChevL,
  IconChevR,
  IconClose,
  IconDownload,
  IconFilm,
  IconPlay,
  IconTrash,
} from "../icons";
import { fmtAgo, fmtBytes, fmtDateTime } from "../lib/format";
import type { CameraInfo, MediaInfo } from "../types";

const PAGE = 12;

function CamFilter({
  cameras,
  value,
  onChange,
}: {
  cameras: CameraInfo[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="filter-scroll">
      <button className={`chip-filter ${!value ? "is-on" : ""}`} onClick={() => onChange("")}>All cameras</button>
      {cameras.map((c) => (
        <button key={`${c.host}/${c.id}`} className={`chip-filter ${value === c.id ? "is-on" : ""}`} onClick={() => onChange(c.id)}>
          {c.name}
        </button>
      ))}
    </div>
  );
}

export function Gallery() {
  const toast = useToast();
  const cameras = useCameras().data ?? [];
  // Media is aggregated across every tailnet node; filter chips show all cameras.
  const camName = (id: string) => cameras.find((c) => c.id === id)?.name ?? id;

  const [cam, setCam] = useState("");
  const [type, setType] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [light, setLight] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<MediaInfo | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);

  const mediaQ = useMedia({ camera_id: cam || undefined, media_type: type || undefined, limit });
  const del = useDeleteMedia();
  const rows = mediaQ.data ?? [];
  const hasMore = rows.length >= limit;

  useEffect(() => setLimit(PAGE), [cam, type]);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasMore) return;
    const io = new IntersectionObserver(
      ([e]) => e.isIntersecting && setLimit((n) => n + PAGE),
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, rows.length]);

  const doDelete = async (m: MediaInfo) => {
    setConfirm(null);
    try {
      await del.mutateAsync({ prefix: m.proxy_prefix, id: m.id });
      toast.ok("Deleted");
      setLight(null);
    } catch {
      toast.err("Delete failed");
    }
  };

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <h1 className="screen-title">Gallery</h1>
          <p className="screen-sub">{rows.length} item{rows.length !== 1 ? "s" : ""} · all devices</p>
        </div>
        <Segmented
          ariaLabel="Media type"
          value={type}
          options={[{ value: "", label: "All" }, { value: "snapshot", label: "Snapshots" }, { value: "recording", label: "Recordings" }]}
          onChange={(v) => setType(v as string)}
        />
      </div>
      <CamFilter cameras={cameras} value={cam} onChange={setCam} />

      {rows.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconFilm size={36} /></div>
          <div className="empty-title">No media yet</div>
          <div className="empty-sub">Snapshots and recordings from any device appear here.</div>
        </div>
      ) : (
        <>
          <div className="media-grid">
            {rows.map((m, i) => (
              <button key={`${m.host}/${m.id}`} className="media-card" onClick={() => setLight(i)} aria-label={`${m.media_type} from ${camName(m.camera_id)}`}>
                <div className="media-thumb">
                  <img className="thumb-canvas" src={m.has_thumbnail ? mediaThumbUrl(m.proxy_prefix, m.id) : mediaFileUrl(m.proxy_prefix, m.id)} alt="" loading="lazy" />
                  {m.media_type === "recording" && <span className="media-play"><IconPlay size={20} /></span>}
                  <span className={`media-type-tag ${m.media_type}`}>{m.media_type === "recording" ? "REC" : "IMG"}</span>
                </div>
                <div className="media-meta">
                  <span className="media-cam">{camName(m.camera_id)} <span className="media-host mono">{m.host}</span></span>
                  <span className="media-time mono">{fmtAgo(m.created_ts)}</span>
                  <span className="media-sub mono">
                    <span className={`trigger trigger-${m.trigger}`}>{m.trigger}</span> · {fmtBytes(m.size_bytes)}
                  </span>
                </div>
              </button>
            ))}
          </div>
          {hasMore && <div ref={sentinel} className="load-more"><Spinner size={16} /> loading more…</div>}
        </>
      )}

      {light != null && rows[light] && (
        <Lightbox
          row={rows[light]}
          camName={camName(rows[light].camera_id)}
          hasPrev={light > 0}
          hasNext={light < rows.length - 1}
          onPrev={() => setLight((i) => Math.max(0, (i ?? 0) - 1))}
          onNext={() => setLight((i) => Math.min(rows.length - 1, (i ?? 0) + 1))}
          onClose={() => setLight(null)}
          onDelete={() => setConfirm(rows[light])}
        />
      )}
      <ConfirmDialog
        open={!!confirm}
        title="Delete media?"
        danger
        confirmLabel="Delete"
        body={confirm ? `This ${confirm.media_type} from ${camName(confirm.camera_id)} will be permanently removed.` : ""}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && doDelete(confirm)}
      />
    </div>
  );
}

function Lightbox({
  row,
  camName,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  onDelete,
}: {
  row: MediaInfo;
  camName: string;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  onDelete: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft" && hasPrev) onPrev();
      if (e.key === "ArrowRight" && hasNext) onNext();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [hasPrev, hasNext, onClose, onPrev, onNext]);

  const isRec = row.media_type === "recording";
  const fileUrl = mediaFileUrl(row.proxy_prefix, row.id);
  return (
    <div className="lb-root" role="dialog" aria-modal="true" aria-label="Media viewer">
      <div className="lb-backdrop" onClick={onClose} />
      <button className="lb-x" onClick={onClose} aria-label="Close"><IconClose size={20} /></button>
      {hasPrev && <button className="lb-nav lb-prev" onClick={onPrev} aria-label="Previous"><IconChevL size={24} /></button>}
      {hasNext && <button className="lb-nav lb-next" onClick={onNext} aria-label="Next"><IconChevR size={24} /></button>}
      <div className="lb-stage">
        <div className="lb-media">
          {isRec ? (
            <video className="lb-canvas" src={fileUrl} controls playsInline poster={row.has_thumbnail ? mediaThumbUrl(row.proxy_prefix, row.id) : undefined} />
          ) : (
            <img className="lb-canvas" src={fileUrl} alt="" />
          )}
        </div>
        <div className="lb-info">
          <div className="lb-info-l">
            <span className="lb-cam">{camName}</span>
            <span className="lb-time mono">{fmtDateTime(row.created_ts)}</span>
            <span className="lb-sub mono">
              <span className={`trigger trigger-${row.trigger}`}>{row.trigger}</span> · {isRec ? "recording" : "snapshot"} · {fmtBytes(row.size_bytes)}
            </span>
          </div>
          <div className="lb-actions">
            <a className="btn btn-outline btn-sm" href={fileUrl} download><span className="btn-ic"><IconDownload size={15} /></span><span>Download</span></a>
            <Button variant="danger" size="sm" icon={<IconTrash size={15} />} onClick={onDelete}>Delete</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
