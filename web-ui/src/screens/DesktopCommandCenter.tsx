import { useQueries } from "@tanstack/react-query";

import { fleetNodeHealthQueryOptions, useCameras, useEvents, useHosts, useRecording, useSnapshot, useSystem } from "../api/hooks";
import { LiveViewer } from "../components/LiveViewer";
import { useToast } from "../components/toast";
import { Button } from "../components/ui";
import { desktopRuntime } from "../desktop/runtime";
import { IconCamShutter, IconRecord, IconRefresh, IconStop, IconWall } from "../icons";
import { fmtAgo } from "../lib/format";
import { summarizeFleetHealth } from "../lib/fleet";
import type { CameraInfo, ViewParams } from "../types";

const PREVIEW_VIEW: ViewParams = { fps: 4, zoom: 1, panX: 0.5, panY: 0.5, quality: 45, w: 280 };

function QuickCamera({ cam }: { cam: CameraInfo }) {
  const toast = useToast();
  const snap = useSnapshot(cam.proxy_prefix, cam.id);
  const recording = useRecording(cam.proxy_prefix, cam.id);
  const busy = snap.isPending || recording.start.isPending || recording.stop.isPending;

  const snapshot = async () => {
    try {
      await snap.mutateAsync();
      toast.ok(`Snapshot saved: ${cam.name}`);
    } catch {
      toast.err("Snapshot failed");
    }
  };

  const toggleRecording = async () => {
    try {
      if (cam.recording) {
        await recording.stop.mutateAsync();
        toast.ok(`Recording stopped: ${cam.name}`);
      } else {
        await recording.start.mutateAsync();
        toast.ok(`Recording started: ${cam.name}`);
      }
    } catch {
      toast.err("Recording action failed");
    }
  };

  return (
    <article className="dcc-preview">
      <div className="dcc-video">
        <LiveViewer cam={cam} view={PREVIEW_VIEW} showOsd={false} fit="cover" />
        <div className="dcc-video-label">
          <span>{cam.name}</span>
          {cam.recording && <span className="tile-rec"><span className="rec-dot" />REC</span>}
        </div>
      </div>
      <div className="dcc-actions">
        <Button size="sm" variant="outline" icon={<IconCamShutter size={14} />} onClick={snapshot} disabled={busy || cam.status === "offline"}>
          Snap
        </Button>
        <Button
          size="sm"
          variant={cam.recording ? "danger" : "ghost"}
          icon={cam.recording ? <IconStop size={14} /> : <IconRecord size={14} />}
          onClick={toggleRecording}
          disabled={busy || cam.status === "offline"}
        >
          {cam.recording ? "Stop" : "Rec"}
        </Button>
      </div>
    </article>
  );
}

export function DesktopCommandCenter() {
  const camerasQ = useCameras();
  const hostsQ = useHosts();
  const system = useSystem().data;
  const events = useEvents({ limit: 1 }).data ?? [];
  const cameras = camerasQ.data ?? [];
  const hosts = hostsQ.data ?? [];
  const healthQueries = useQueries({
    queries: hosts.map((host) => fleetNodeHealthQueryOptions(host.node_key, host.online)),
  });
  const summary = summarizeFleetHealth(
    hosts.map((host, i) => ({
      host,
      health: healthQueries[i]?.data,
      error: healthQueries[i]?.error,
    })),
    system?.version,
  );
  const previews = cameras.filter((cam) => cam.status !== "offline").slice(0, 2);
  const recordingCount = cameras.filter((cam) => cam.recording).length;
  const newest = events[0];

  return (
    <main className="desktop-cc">
      <header className="dcc-head">
        <div>
          <span className="microlabel lit">TailCam Command Center</span>
          <h1>Fleet Live</h1>
        </div>
        <span className={`led ${summary.offline || summary.error ? "err" : summary.warning ? "warn" : "ok"}`} />
      </header>

      <section className="dcc-strip">
        <div><span className="microlabel">Healthy</span><b>{summary.healthy}</b></div>
        <div><span className="microlabel">Warn</span><b>{summary.warning}</b></div>
        <div><span className="microlabel">Offline</span><b>{summary.offline + summary.error}</b></div>
        <div><span className="microlabel">Rec</span><b>{recordingCount}</b></div>
      </section>

      <section className="dcc-previews">
        {previews.length ? (
          previews.map((cam) => <QuickCamera key={`${cam.host}/${cam.id}`} cam={cam} />)
        ) : (
          <div className="dcc-empty">
            {camerasQ.isLoading ? "Loading cameras..." : "No online cameras available."}
          </div>
        )}
      </section>

      <section className="dcc-event">
        <span className="microlabel">Newest event</span>
        {newest ? (
          <div>
            <strong>{newest.label ?? "Motion"}</strong>
            <span className="mono">{newest.camera_id} · {fmtAgo(newest.start_ts)}</span>
          </div>
        ) : (
          <span className="mono">No motion events yet.</span>
        )}
      </section>

      <footer className="dcc-foot">
        <Button variant="primary" icon={<IconWall size={15} />} onClick={() => desktopRuntime.openMainWindow()}>
          Open TailCam
        </Button>
        <Button variant="ghost" icon={<IconRefresh size={15} className={hostsQ.isFetching ? "spin" : ""} />} onClick={() => window.location.reload()}>
          Refresh
        </Button>
        <Button variant="danger" onClick={() => desktopRuntime.quit()}>
          Quit
        </Button>
      </footer>
    </main>
  );
}
