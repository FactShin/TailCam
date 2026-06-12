import { useNavigate } from "react-router-dom";

import { useCameras, useHosts, useRefreshCameras, useRestoreHidden, useSystem } from "../api/hooks";
import { CameraTile } from "../components/CameraTile";
import { Button } from "../components/ui";
import { useToast } from "../components/toast";
import { IconCamera, IconFps, IconRefresh, IconServer } from "../icons";
import { cameraPath } from "../lib/nav";
import type { CameraInfo, HostInfo } from "../types";

export function Dashboard() {
  const navigate = useNavigate();
  const toast = useToast();
  const camerasQ = useCameras();
  const system = useSystem().data;
  const restoreHidden = useRestoreHidden();
  const hostsQ = useHosts();
  const refresh = useRefreshCameras();

  const cameras = camerasQ.data ?? [];
  const hosts = hostsQ.data ?? [];
  const online = cameras.filter((c) => c.status === "online").length;

  const doRefresh = async () => {
    try {
      await refresh.mutateAsync();
      toast.ok("Devices re-scanned");
    } catch {
      toast.err("Re-scan failed");
    }
  };

  if (camerasQ.isLoading) {
    return (
      <div className="screen">
        <div className="grid" data-tile="cinematic">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="tile tile-skeleton" />
          ))}
        </div>
      </div>
    );
  }

  if (camerasQ.isError && cameras.length === 0) {
    return (
      <div className="screen">
        <div className="empty">
          <div className="empty-ic"><IconCamera size={40} /></div>
          <div className="empty-title">Can't reach AnyCam</div>
          <div className="empty-sub">
            The server isn't responding. Check that AnyCam is running and you're connected to your tailnet.
          </div>
          <Button variant="primary" icon={<IconRefresh size={16} />} onClick={() => camerasQ.refetch()}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  // Group cameras by host, ordered local-first using the hosts list.
  const orderedHosts: HostInfo[] = hosts.length
    ? hosts
    : cameras.length
      ? [{ host: cameras[0].host, kind: "local", online: true, version: null, camera_count: cameras.length, proxy_prefix: "" }]
      : [];
  const byHost = (h: string) => cameras.filter((c) => c.host === h);
  const multiHost = orderedHosts.length > 1;

  return (
    <div className="screen">
      <div className="screen-head">
        <div>
          <h1 className="screen-title">Cameras</h1>
          <p className="screen-sub">
            {cameras.length} camera{cameras.length !== 1 ? "s" : ""}
            {multiHost ? ` across ${orderedHosts.length} devices` : ""} ·{" "}
            <span style={{ color: "var(--ok)" }}>{online} online</span>
            <span className="bw-note" title="Grid tiles request a low-bandwidth stream (8 fps, 480px) and pause when off-screen or the tab is hidden.">
              <IconFps size={13} /> low-bandwidth grid
            </span>
            {camerasQ.isFetching && <span className="updating"> · updating…</span>}
          </p>
        </div>
        <div className="head-actions">
          {system && system.hidden_count > 0 && (
            <Button
              variant="ghost"
              onClick={async () => {
                try {
                  await restoreHidden.mutateAsync();
                  toast.ok("Hidden cameras restored");
                } catch {
                  toast.err("Restore failed");
                }
              }}
              disabled={restoreHidden.isPending}
            >
              Restore hidden ({system.hidden_count})
            </Button>
          )}
          <Button
            variant="outline"
            icon={<IconRefresh size={16} className={refresh.isPending ? "spin" : ""} />}
            onClick={doRefresh}
            disabled={refresh.isPending}
          >
            {refresh.isPending ? "Scanning…" : "Refresh devices"}
          </Button>
        </div>
      </div>

      {cameras.length === 0 ? (
        <div className="empty">
          <div className="empty-ic"><IconCamera size={40} /></div>
          <div className="empty-title">No cameras found</div>
          <div className="empty-sub">Plug in a USB camera on any AnyCam device on your tailnet, then re-scan.</div>
          <Button variant="primary" icon={<IconRefresh size={16} />} onClick={doRefresh}>Refresh devices</Button>
        </div>
      ) : (
        orderedHosts.map((h) => {
          const cams = byHost(h.host);
          if (!cams.length && h.online) return null;
          const liveCount = cams.filter((c) => c.status === "online").length;
          return (
            <section key={h.host} className="host-group">
              {multiHost && (
                <header className="host-head">
                  <span className="host-name">
                    <IconServer size={15} /> {h.host}
                    {h.kind === "local" && <span className="host-tag">this device</span>}
                  </span>
                  <span className={`host-status ${h.online && liveCount ? "ok" : "off"}`}>
                    {h.online ? `${liveCount}/${cams.length} online` : "node offline"}
                  </span>
                </header>
              )}
              <div className="grid" data-tile="cinematic">
                {cams.map((c: CameraInfo) => (
                  <CameraTile key={`${c.host}/${c.id}`} cam={c} onOpen={() => navigate(cameraPath(c))} />
                ))}
              </div>
            </section>
          );
        })
      )}
    </div>
  );
}
