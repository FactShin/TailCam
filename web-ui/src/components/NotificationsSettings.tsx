import { useEffect, useState } from "react";

import { useNotifications, useTestNotification, useUpdateNotifications } from "../api/hooks";
import { IconBell } from "../icons";
import type { NotificationsUpdate } from "../types";
import { useToast } from "./toast";
import { Button, Toggle } from "./ui";

export function NotificationsSettings() {
  const data = useNotifications().data;
  const update = useUpdateNotifications();
  const test = useTestNotification();
  const toast = useToast();

  const [form, setForm] = useState<NotificationsUpdate>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (data && !dirty) {
      const { channels: _drop, ...rest } = data;
      setForm(rest);
    }
  }, [data, dirty]);

  const set = (patch: NotificationsUpdate) => {
    setForm((f) => ({ ...f, ...patch }));
    setDirty(true);
  };

  const hasChannel = !!(
    form.discord_webhook ||
    form.webhook_url ||
    (form.telegram_token && form.telegram_chat_id)
  );

  const save = async () => {
    try {
      await update.mutateAsync(form);
      setDirty(false);
      toast.ok("Notifications saved");
    } catch {
      toast.err("Could not save");
    }
  };

  const sendTest = async () => {
    try {
      if (dirty) {
        await update.mutateAsync(form);
        setDirty(false);
      }
      const r = await test.mutateAsync();
      toast.ok(r.detail || "Test sent");
    } catch (e) {
      toast.err(e instanceof Error ? e.message : "Test failed — check your channels");
    }
  };

  const enabled = !!form.enabled;
  const labelsStr = (form.labels ?? []).join(", ");
  const conf = form.min_confidence ?? 0;

  return (
    <div className="panel notif-panel">
      <div className="panel-title">
        <IconBell size={16} /> Notifications
        <span style={{ flex: 1 }} />
        <Toggle checked={enabled} label="Enable notifications" onChange={(v) => set({ enabled: v })} />
      </div>
      <p className="ais-intro">
        Get a ping on Discord, Telegram, or your own bot (Hermes/OpenClaw, via the generic webhook)
        when motion is detected, a camera/node drops, or training finishes.
        {(data?.channels.length ?? 0) > 0
          ? ` Active: ${data?.channels.join(", ")}.`
          : " No channels set up yet."}
      </p>

      {enabled && (
        <>
          <div className="notif-grid">
            <label className="tl-field">
              <span className="microlabel">Discord webhook URL</span>
              <input className="tl-input" value={form.discord_webhook ?? ""}
                placeholder="https://discord.com/api/webhooks/…"
                onChange={(e) => set({ discord_webhook: e.target.value })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Generic webhook — your bot</span>
              <input className="tl-input" value={form.webhook_url ?? ""}
                placeholder="https://your-bot.example/ingest"
                onChange={(e) => set({ webhook_url: e.target.value })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Telegram bot token</span>
              <input className="tl-input" value={form.telegram_token ?? ""}
                placeholder="123456:ABC-DEF…"
                onChange={(e) => set({ telegram_token: e.target.value })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Telegram chat id</span>
              <input className="tl-input" value={form.telegram_chat_id ?? ""}
                placeholder="e.g. 987654321"
                onChange={(e) => set({ telegram_chat_id: e.target.value })} />
            </label>
          </div>

          <div className="notif-row">
            <span className="microlabel">Notify me about</span>
            <div className="notif-trigs">
              <span className="notif-trig">
                <Toggle checked={!!form.notify_motion} label="Motion + AI label"
                  onChange={(v) => set({ notify_motion: v })} />
                <span>Motion + AI label</span>
              </span>
              <span className="notif-trig">
                <Toggle checked={!!form.notify_camera_offline} label="Camera / node offline"
                  onChange={(v) => set({ notify_camera_offline: v })} />
                <span>Camera / node offline</span>
              </span>
              <span className="notif-trig">
                <Toggle checked={!!form.notify_training} label="Training updates"
                  onChange={(v) => set({ notify_training: v })} />
                <span>Training updates</span>
              </span>
            </div>
          </div>

          <div className="notif-grid">
            <label className="tl-field">
              <span className="microlabel">Min AI confidence — {Math.round(conf * 100)}%</span>
              <input type="range" min={0} max={1} step={0.05} value={conf}
                onChange={(e) => set({ min_confidence: parseFloat(e.target.value) })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Only these labels (comma-sep · blank = all)</span>
              <input className="tl-input" value={labelsStr} placeholder="person, vehicle"
                onChange={(e) => set({ labels: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </label>
            <label className="tl-field">
              <span className="microlabel">Cooldown per camera (seconds)</span>
              <input className="tl-input" type="number" min={0} value={form.cooldown_seconds ?? 60}
                onChange={(e) => set({ cooldown_seconds: parseFloat(e.target.value) || 0 })} />
            </label>
          </div>

          <div className="notif-actions">
            <Button variant="primary" disabled={!dirty || update.isPending} onClick={save}>
              {update.isPending ? "Saving…" : "Save"}
            </Button>
            <Button variant="outline" disabled={!hasChannel || test.isPending} onClick={sendTest}>
              {test.isPending ? "Sending…" : "Send test"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
