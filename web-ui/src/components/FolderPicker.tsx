import { useState } from "react";

import { useBrowseFs } from "../api/hooks";
import { IconChevL, IconClose, IconHdd } from "../icons";
import { fmtBytes } from "../lib/format";
import { Button, Spinner } from "./ui";

/** Browse a node's folders (through the proxy for a peer) and pick one — so
 * the save location never has to be typed from memory, even from your phone. */
export function FolderPicker({
  prefix,
  host,
  initialPath,
  onPick,
  onClose,
}: {
  prefix: string; // "" = this device, "/proxy/<key>" = a peer
  host: string;
  initialPath: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [showHidden, setShowHidden] = useState(false);
  const q = useBrowseFs(prefix, path, showHidden, true);
  const data = q.data;

  return (
    <div className="fp-root" role="dialog" aria-label="Choose a folder" onClick={onClose}>
      <div className="navsheet-backdrop" />
      <div className="navsheet folder-picker" onClick={(e) => e.stopPropagation()}>
        <div className="navsheet-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <IconHdd size={16} /> Choose a folder on {host}
          <span style={{ flex: 1 }} />
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close"><IconClose size={15} /></button>
        </div>

        <div className="fp-path">
          <button
            className="btn btn-ghost btn-sm"
            disabled={!data?.parent && !path}
            onClick={() => setPath(data?.parent ?? "")}
            aria-label="Up one folder"
          >
            <IconChevL size={15} />
          </button>
          <input
            className="tl-input mono"
            value={path}
            placeholder="Drives and starting points"
            onChange={(e) => setPath(e.target.value)}
          />
        </div>

        {q.isLoading && <div className="fp-empty"><Spinner size={18} /></div>}
        {data?.error && <div className="fp-error mono">{data.error}</div>}

        {data && !path && (
          <div className="fp-list">
            {data.roots.map((r) => (
              <button key={r.path} className="fp-row" onClick={() => setPath(r.path)}>
                <IconHdd size={14} /> <span className="fp-name">{r.name}</span>
                <span className="fp-sub mono">{r.path}</span>
              </button>
            ))}
          </div>
        )}
        {data && path && (
          <div className="fp-list">
            {data.entries.length === 0 && data.exists && (
              <div className="fp-empty mono">No sub-folders</div>
            )}
            {data.entries.map((e) => (
              <button key={e.path} className="fp-row" onClick={() => setPath(e.path)}>
                <span className="fp-name">{e.name}</span>
                {!e.writable && <span className="ais-badge warn">read-only</span>}
              </button>
            ))}
          </div>
        )}

        <div className="fp-foot">
          <label className="fp-hidden">
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> hidden folders
          </label>
          {data?.exists && data.disk_total > 0 && (
            <span className="mono fp-disk">{fmtBytes(data.disk_free)} free of {fmtBytes(data.disk_total)}</span>
          )}
          <span style={{ flex: 1 }} />
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!path || (data ? !data.writable && data.exists : true)}
            onClick={() => onPick(path)}
          >
            Use this folder
          </Button>
        </div>
      </div>
    </div>
  );
}
