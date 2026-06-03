// screens2.jsx — Gallery (+ lightbox), Events feed, Settings

const { useState: useS2, useEffect: useE2, useRef: useR2, useCallback: useC2 } = React;

// frozen-frame thumbnail (stands in for /media/{id}/thumbnail)
function MediaThumb({ cameraId, seed = 1, className = "" }) {
  const ref = useR2(null);
  useE2(() => {
    const c = ref.current; if (!c) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const r = c.getBoundingClientRect();
    c.width = Math.max(2, r.width * dpr); c.height = Math.max(2, r.height * dpr);
    const cam = api.camera(cameraId) || { id: cameraId, properties: {}, transform: { rotation: 0, flip_h: false, flip_v: false }, status: "online" };
    renderFeed(c, cam, { t: seed * 3.1, zoom: 1, panX: 0.5, panY: 0.5,
      brightness: cam.properties.brightness ?? 50, contrast: cam.properties.contrast ?? 50,
      rotation: cam.transform.rotation, flipH: cam.transform.flip_h, flipV: cam.transform.flip_v, dim: false });
  }, [cameraId, seed]);
  return <canvas ref={ref} className={className} />;
}

function CamFilter({ cameras, value, onChange, includeAll = true }) {
  return (
    <div className="filter-scroll">
      {includeAll && <button className={`chip-filter ${!value ? "is-on" : ""}`} onClick={() => onChange("")}>All cameras</button>}
      {cameras.map((c) => (
        <button key={c.id} className={`chip-filter ${value === c.id ? "is-on" : ""}`} onClick={() => onChange(c.id)}>{c.name}</button>
      ))}
    </div>
  );
}

// ===========================================================================
// Gallery
// ===========================================================================
const PAGE = 12;
function Gallery() {
  const cameras = useCameras();
  const toast = useToast();
  const [cam, setCam] = useS2("");
  const [type, setType] = useS2("");
  const [count, setCount] = useS2(PAGE);
  const [light, setLight] = useS2(null); // index into rows
  const [confirm, setConfirm] = useS2(null);
  const [tick, setTick] = useS2(0); // re-read after delete
  const sentinel = useR2(null);

  useE2(() => setCount(PAGE), [cam, type]);
  useE2(() => api.subscribe(() => setTick((n) => n + 1)), []);

  const all = api.media({ camera_id: cam || undefined, media_type: type || undefined, limit: 500 });
  const rows = all.slice(0, count);
  const hasMore = all.length > count;

  // infinite scroll
  useE2(() => {
    const el = sentinel.current; if (!el || !hasMore) return;
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) setCount((c) => c + PAGE); }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, rows.length]);

  const del = async (mid) => {
    setConfirm(null);
    try { await api.deleteMedia(mid); toast.ok("Deleted"); setLight(null); }
    catch { toast.err("Delete failed"); }
  };

  return (
    <div className="screen">
      <div className="screen-head">
        <div><h1 className="screen-title">Gallery</h1><p className="screen-sub">{all.length} item{all.length !== 1 ? "s" : ""}</p></div>
        <Segmented ariaLabel="Media type" value={type}
          options={[{ value: "", label: "All" }, { value: "snapshot", label: "Snapshots" }, { value: "recording", label: "Recordings" }]}
          onChange={setType} />
      </div>
      <CamFilter cameras={cameras} value={cam} onChange={setCam} />

      {rows.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconFilm size={36} /></div>
          <div className="empty-title">No media yet</div>
          <div className="empty-sub">Snapshots and recordings will appear here.</div>
        </div>
      ) : (
        <>
          <div className="media-grid">
            {rows.map((m, i) => (
              <button key={m.id} className="media-card" onClick={() => setLight(i)} aria-label={`${m.media_type} from ${camName(m.camera_id)}`}>
                <div className="media-thumb">
                  <MediaThumb cameraId={m.camera_id} seed={m.id} className="thumb-canvas" />
                  {m.media_type === "recording" && <span className="media-play"><IconPlay size={20} /></span>}
                  <span className={`media-type-tag ${m.media_type}`}>{m.media_type === "recording" ? "REC" : "IMG"}</span>
                </div>
                <div className="media-meta">
                  <span className="media-cam">{camName(m.camera_id)}</span>
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
        <Lightbox row={rows[light]} hasPrev={light > 0} hasNext={light < rows.length - 1}
          onPrev={() => setLight((i) => Math.max(0, i - 1))} onNext={() => setLight((i) => Math.min(rows.length - 1, i + 1))}
          onClose={() => setLight(null)} onDelete={() => setConfirm(rows[light])} />
      )}
      <ConfirmDialog open={!!confirm} title="Delete media?" danger confirmLabel="Delete"
        body={confirm ? `This ${confirm.media_type} from ${camName(confirm.camera_id)} will be permanently removed.` : ""}
        onCancel={() => setConfirm(null)} onConfirm={() => del(confirm.id)} />
    </div>
  );
}

function Lightbox({ row, onClose, onPrev, onNext, hasPrev, hasNext, onDelete }) {
  useE2(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft" && hasPrev) onPrev();
      if (e.key === "ArrowRight" && hasNext) onNext();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [hasPrev, hasNext, onClose, onPrev, onNext]);
  const isRec = row.media_type === "recording";
  return (
    <div className="lb-root" role="dialog" aria-modal="true" aria-label="Media viewer">
      <div className="lb-backdrop" onClick={onClose} />
      <button className="lb-x" onClick={onClose} aria-label="Close"><IconClose size={20} /></button>
      {hasPrev && <button className="lb-nav lb-prev" onClick={onPrev} aria-label="Previous"><IconChevL size={24} /></button>}
      {hasNext && <button className="lb-nav lb-next" onClick={onNext} aria-label="Next"><IconChevR size={24} /></button>}
      <div className="lb-stage">
        <div className="lb-media">
          {isRec ? (
            <div className="lb-video">
              <MediaThumb cameraId={row.camera_id} seed={row.id} className="lb-canvas" />
              <div className="lb-video-ov">
                <span className="lb-play"><IconPlay size={28} /></span>
                <p className="lb-video-note">Recording preview · <span className="mono">.mp4</span> may not play in every browser</p>
                <a className="btn btn-primary btn-md" href={mediaFileUrl(row.id)} download>
                  <span className="btn-ic"><IconDownload size={16} /></span><span>Download clip</span>
                </a>
              </div>
            </div>
          ) : (
            <MediaThumb cameraId={row.camera_id} seed={row.id} className="lb-canvas" />
          )}
        </div>
        <div className="lb-info">
          <div className="lb-info-l">
            <span className="lb-cam">{camName(row.camera_id)}</span>
            <span className="lb-time mono">{fmtDateTime(row.created_ts)}</span>
            <span className="lb-sub mono">
              <span className={`trigger trigger-${row.trigger}`}>{row.trigger}</span> · {isRec ? "recording" : "snapshot"} · {fmtBytes(row.size_bytes)}
            </span>
          </div>
          <div className="lb-actions">
            <a className="btn btn-outline btn-sm" href={mediaFileUrl(row.id)} download><span className="btn-ic"><IconDownload size={15} /></span><span>Download</span></a>
            <Button variant="danger" size="sm" icon={<IconTrash size={15} />} onClick={onDelete}>Delete</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// Events
// ===========================================================================
function Events() {
  const cameras = useCameras();
  const [cam, setCam] = useS2("");
  const events = useEventsFeed(cam ? { camera_id: cam, limit: 80 } : { limit: 80 });
  const nav = window.useNavigate();
  const [now, setNow] = useS2(NOW());
  useE2(() => { const t = setInterval(() => setNow(NOW()), 1000); return () => clearInterval(t); }, []);

  return (
    <div className="screen">
      <div className="screen-head">
        <div><h1 className="screen-title">Motion events</h1><p className="screen-sub">{events.length} event{events.length !== 1 ? "s" : ""} · newest first · live</p></div>
      </div>
      <CamFilter cameras={cameras} value={cam} onChange={setCam} />

      {events.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconMotion size={36} /></div>
          <div className="empty-title">No motion events</div>
          <div className="empty-sub">Events appear when motion-enabled cameras detect movement.</div>
        </div>
      ) : (
        <div className="event-list">
          {events.map((e) => {
            const ongoing = e.end_ts == null;
            const dur = ongoing ? now - e.start_ts : e.end_ts - e.start_ts;
            const pct = Math.round(e.peak_score * 100);
            const sev = pct >= 75 ? "high" : pct >= 50 ? "mid" : "low";
            return (
              <div key={e.id} className={`event-row ${ongoing ? "is-live" : ""}`}>
                <div className="event-score">
                  <div className={`score-ring score-${sev}`} style={{ "--p": pct }}><span className="mono">{pct}%</span></div>
                </div>
                <div className="event-body">
                  <div className="event-l1">
                    <span className="event-cam">{camName(e.camera_id)}</span>
                    {ongoing && <span className="event-ongoing"><span className="rec-dot" /> ongoing</span>}
                  </div>
                  <div className="event-l2 mono">
                    {fmtDateTime(e.start_ts)} · {fmtDur(dur)}{ongoing ? "" : ""} · peak {pct}%
                  </div>
                </div>
                <div className="event-actions">
                  {e.recording_id != null ? (
                    <Button variant="outline" size="sm" icon={<IconPlay size={14} />} onClick={() => nav("/gallery")}>View clip</Button>
                  ) : (
                    <span className="event-noclip mono">no clip</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Settings
// ===========================================================================
function Settings() {
  const sys = useSystem();
  const cameras = useCameras();
  const toast = useToast();
  const [copied, setCopied] = useS2(false);
  const copy = (text) => {
    try { navigator.clipboard.writeText(text); } catch {}
    setCopied(true); toast.ok("Copied access URL"); setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="screen">
      <div className="screen-head"><div><h1 className="screen-title">Settings</h1><p className="screen-sub">System &amp; access</p></div></div>

      <div className="settings-grid">
        <div className="panel">
          <div className="panel-title"><IconInfo size={16} /> System</div>
          <div className="kv"><span className="kv-k">Version</span><span className="kv-v mono">AnyCam {sys.version}</span></div>
          <div className="kv"><span className="kv-k">Cameras</span><span className="kv-v mono">{cameras.length} connected</span></div>
          <div className="kv"><span className="kv-k">Storage used</span><span className="kv-v mono">{fmtBytes(sys.media_bytes)}</span></div>
        </div>

        <div className="panel">
          <div className="panel-title">{sys.tailscale_running ? <IconWifi size={16} /> : <IconWifiOff size={16} />} Tailscale</div>
          <div className="kv">
            <span className="kv-k">Status</span>
            <span className="kv-v">
              {sys.tailscale_running
                ? <span className="badge badge-ok"><span className="pill-dot" style={{ background: "var(--ok)" }} /> Running</span>
                : sys.tailscale_installed
                  ? <span className="badge badge-warn"><span className="pill-dot" style={{ background: "var(--warn)" }} /> Installed · stopped</span>
                  : <span className="badge badge-err"><span className="pill-dot" style={{ background: "var(--err)" }} /> Not installed</span>}
            </span>
          </div>
          <div className="kv kv-stack">
            <span className="kv-k">Access URL (private)</span>
            <div className="url-row">
              <code className="url-code mono">{sys.access_url}</code>
              <button className="copy-btn" onClick={() => copy(sys.access_url)} aria-label="Copy access URL">{copied ? <IconCheck size={16} /> : <IconCopy size={16} />}</button>
            </div>
          </div>
          <div className="kv kv-stack">
            <span className="kv-k">Local URL</span>
            <code className="url-code mono">{sys.local_url}</code>
          </div>
        </div>

        <div className="panel panel-help">
          <div className="panel-title"><IconDevice size={16} /> Access from another device</div>
          <ol className="help-list">
            <li>Install <span className="mono">Tailscale</span> on your phone or laptop and sign in to the same tailnet.</li>
            <li>Open the private access URL above in any browser — no password, the network is the boundary.</li>
            <li>Add AnyCam to your home screen to install it as an app (fullscreen, offline app-shell).</li>
          </ol>
          <p className="help-foot mono">No accounts · no tokens · no telemetry. Security is handled by Tailscale.</p>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Gallery, Events, Settings, Lightbox, MediaThumb });
