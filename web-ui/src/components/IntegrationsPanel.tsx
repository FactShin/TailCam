import { useEffect, useState } from "react";

import {
  useIntegrations,
  useResetHomeKit,
  useUpdateHomeAssistant,
  useUpdateHomeKit,
} from "../api/hooks";
import { IconCopy, IconDevice, IconPhone, IconServer } from "../icons";
import type { HomeAssistantStatus, HomeKitStatus } from "../types";
import { useToast } from "./toast";
import { Button, Toggle } from "./ui";

function useCopy() {
  const toast = useToast();
  return (text: string, label = "Copied") => {
    navigator.clipboard
      ?.writeText(text)
      .then(() => toast.ok(`${label} copied`))
      .catch(() => toast.err("Copy failed"));
  };
}

function Badge({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <span className={`ais-badge ${ok ? "ok" : "rec"}`}>{children}</span>;
}

// ---------------------------------------------------------------- HomeKit
function HomeKitCard({ hk }: { hk: HomeKitStatus }) {
  const update = useUpdateHomeKit();
  const reset = useResetHomeKit();
  const toast = useToast();

  const allIds = hk.cameras.map((c) => c.id);
  const [name, setName] = useState(hk.bridge_name);
  // Explicit checked-id list in the UI. The API's "[] = all cameras" sentinel
  // is only applied on save — otherwise unchecking the last camera would
  // collapse to [] and silently re-select everything.
  const [sel, setSel] = useState<string[]>(hk.selected.length === 0 ? allIds : hk.selected);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!dirty) {
      setName(hk.bridge_name);
      setSel(hk.selected.length === 0 ? hk.cameras.map((c) => c.id) : hk.selected);
    }
  }, [hk.bridge_name, hk.selected, hk.cameras, dirty]);

  const checked = (id: string) => sel.includes(id);
  const toggleCam = (id: string) => {
    setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
    setDirty(true);
  };
  const noneSelected = hk.cameras.length > 0 && sel.length === 0;

  const setEnabled = (v: boolean) => update.mutate({ enabled: v });
  const save = async () => {
    try {
      await update.mutateAsync({
        bridge_name: name,
        cameras: sel.length === allIds.length ? [] : sel,
      });
      setDirty(false);
      toast.ok("HomeKit updated");
    } catch {
      toast.err("Could not update HomeKit");
    }
  };

  return (
    <div className="panel notif-panel">
      <div className="panel-title">
        <IconPhone size={16} /> Apple HomeKit
        <span style={{ flex: 1 }} />
        <Toggle
          checked={hk.enabled}
          label="Enable HomeKit"
          disabled={!hk.available}
          onChange={setEnabled}
        />
      </div>
      <p className="ais-intro">
        Add your cameras to Apple&apos;s Home app on iPhone, iPad &amp; Mac — and view them remotely
        through a Home Hub (HomePod / Apple TV). Cameras pair over HAP, the native path Apple Home
        uses for live video (Matter does not carry camera streams).
      </p>

      {!hk.available && (
        <div className="intg-warn">
          HomeKit support isn&apos;t installed. Run <code>pip install &apos;tailcam[homekit]&apos;</code> and
          restart.
        </div>
      )}

      {hk.enabled && hk.available && (
        <>
          <div className="intg-status">
            <Badge ok={hk.running}>{hk.running ? "running" : "starting…"}</Badge>
            <Badge ok={hk.paired}>{hk.paired ? "paired" : "not paired"}</Badge>
            {!hk.ffmpeg_present && (
              <span className="ais-badge warn">ffmpeg missing — snapshots only</span>
            )}
          </div>

          {!hk.paired && (
            <div className="hk-pair">
              {hk.setup_qr ? (
                <div className="hk-qr" dangerouslySetInnerHTML={{ __html: hk.setup_qr }} />
              ) : (
                <div className="hk-qr hk-qr-empty">starting…</div>
              )}
              <div className="hk-pair-info">
                <div className="microlabel">Scan in the Home app, or enter the setup code</div>
                <div className="hk-pin">{hk.pin || "— — —"}</div>
                <div className="hk-pair-steps">
                  Home app → <b>+</b> → Add Accessory → <b>More options…</b> → pick{" "}
                  <b>{hk.bridge_name}</b>.
                </div>
              </div>
            </div>
          )}

          <div className="notif-grid">
            <label className="tl-field">
              <span className="microlabel">Bridge name (shown in Home)</span>
              <input
                className="tl-input"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setDirty(true);
                }}
              />
            </label>
          </div>

          <div className="notif-row">
            <span className="microlabel">Cameras to expose</span>
            <div className="intg-cams">
              {hk.cameras.map((c) => (
                <label key={c.id} className="intg-cam">
                  <input type="checkbox" checked={checked(c.id)} onChange={() => toggleCam(c.id)} />
                  <span>{c.name}</span>
                </label>
              ))}
              {hk.cameras.length === 0 && <span className="ais-intro">No cameras detected yet.</span>}
            </div>
            {noneSelected && (
              <span className="intg-hint">
                Select at least one camera — to expose none, turn HomeKit off instead.
              </span>
            )}
          </div>

          <div className="notif-actions">
            <Button variant="primary" disabled={!dirty || noneSelected || update.isPending} onClick={save}>
              {update.isPending ? "Saving…" : "Save"}
            </Button>
            <Button
              variant="outline"
              disabled={update.isPending}
              onClick={() => update.mutate({ regenerate_pin: true })}
            >
              New setup code
            </Button>
            <Button
              variant="ghost"
              disabled={reset.isPending}
              onClick={() => {
                reset.mutate();
                toast.ok("Pairing reset");
              }}
            >
              {reset.isPending ? "Resetting…" : "Reset pairing"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// -------------------------------------------------------- Home Assistant
function HomeAssistantCard({ ha }: { ha: HomeAssistantStatus }) {
  const update = useUpdateHomeAssistant();
  const toast = useToast();
  const copy = useCopy();

  const [form, setForm] = useState({
    mqtt_host: ha.mqtt_host,
    mqtt_port: ha.mqtt_port,
    mqtt_username: ha.mqtt_username,
    mqtt_password: "",
    mqtt_tls: ha.mqtt_tls,
    publish_motion: ha.publish_motion,
    publish_status: ha.publish_status,
  });
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!dirty)
      setForm({
        mqtt_host: ha.mqtt_host,
        mqtt_port: ha.mqtt_port,
        mqtt_username: ha.mqtt_username,
        mqtt_password: "",
        mqtt_tls: ha.mqtt_tls,
        publish_motion: ha.publish_motion,
        publish_status: ha.publish_status,
      });
  }, [ha, dirty]);

  const set = (patch: Partial<typeof form>) => {
    setForm((f) => ({ ...f, ...patch }));
    setDirty(true);
  };
  const save = async () => {
    try {
      // Omit the password unless the user typed one — the field shows
      // "(unchanged)" and sending "" would erase the stored password.
      const { mqtt_password, ...rest } = form;
      await update.mutateAsync(mqtt_password ? { ...rest, mqtt_password } : rest);
      setDirty(false);
      toast.ok("Home Assistant updated");
    } catch {
      toast.err("Could not update");
    }
  };

  return (
    <div className="panel notif-panel">
      <div className="panel-title">
        <IconServer size={16} /> Home Assistant
        <span style={{ flex: 1 }} />
        <Toggle checked={ha.enabled} label="Enable" onChange={(v) => update.mutate({ enabled: v })} />
      </div>
      <p className="ais-intro">
        Add cameras to Home Assistant with its built-in <b>MJPEG IP Camera</b> integration using the
        URLs below. Optionally publish motion &amp; connectivity over MQTT so HA automations can react
        to TailCam events.
      </p>

      {ha.enabled && (
        <>
          <div className="intg-cams-list">
            {ha.cameras.map((c) => (
              <div key={c.camera_id} className="intg-url-row">
                <span className="intg-url-name">{c.name}</span>
                <button className="intg-url" onClick={() => copy(c.mjpeg_url, "Stream URL")}>
                  <IconCopy size={13} /> stream
                </button>
                <button className="intg-url" onClick={() => copy(c.still_image_url, "Snapshot URL")}>
                  <IconCopy size={13} /> snapshot
                </button>
              </div>
            ))}
            {ha.cameras.length === 0 && <span className="ais-intro">No cameras detected yet.</span>}
          </div>
          <div className="notif-actions">
            <Button variant="outline" onClick={() => copy(ha.yaml, "YAML")}>
              <IconCopy size={14} /> Copy configuration.yaml
            </Button>
          </div>

          <div className="notif-row">
            <span className="microlabel">
              MQTT discovery (optional) — for automations{" "}
              {ha.mqtt_configured && <Badge ok={ha.mqtt_connected}>{ha.mqtt_connected ? "connected" : "disconnected"}</Badge>}
              {!ha.mqtt_available && <span className="ais-badge warn">paho-mqtt not installed</span>}
            </span>
          </div>
          <div className="notif-grid">
            <label className="tl-field">
              <span className="microlabel">Broker host (blank = off)</span>
              <input className="tl-input" value={form.mqtt_host} placeholder="homeassistant.local"
                onChange={(e) => set({ mqtt_host: e.target.value })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Broker port</span>
              <input className="tl-input" type="number" value={form.mqtt_port}
                onChange={(e) => set({ mqtt_port: parseInt(e.target.value) || 1883 })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Username</span>
              <input className="tl-input" value={form.mqtt_username}
                onChange={(e) => set({ mqtt_username: e.target.value })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Password</span>
              <input className="tl-input" type="password" value={form.mqtt_password} placeholder="(unchanged)"
                onChange={(e) => set({ mqtt_password: e.target.value })} />
            </label>
          </div>
          <div className="notif-trigs">
            <span className="notif-trig">
              <Toggle checked={form.mqtt_tls} label="TLS" onChange={(v) => set({ mqtt_tls: v })} />
              <span>TLS</span>
            </span>
            <span className="notif-trig">
              <Toggle checked={form.publish_motion} label="Motion" onChange={(v) => set({ publish_motion: v })} />
              <span>Publish motion</span>
            </span>
            <span className="notif-trig">
              <Toggle checked={form.publish_status} label="Connectivity" onChange={(v) => set({ publish_status: v })} />
              <span>Publish connectivity</span>
            </span>
          </div>
          <div className="notif-actions">
            <Button variant="primary" disabled={!dirty || update.isPending} onClick={save}>
              {update.isPending ? "Saving…" : "Save MQTT"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

export function IntegrationsPanel() {
  const data = useIntegrations().data;
  if (!data) return null;
  return (
    <div className="intg-wrap">
      <div className="intg-head">
        <IconDevice size={16} /> Home automation
      </div>
      <HomeKitCard hk={data.homekit} />
      <HomeAssistantCard ha={data.homeassistant} />
    </div>
  );
}
