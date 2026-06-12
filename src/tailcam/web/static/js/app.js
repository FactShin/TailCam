/* AnyCam front-end: vanilla JS, no build step. */
const AnyCam = (() => {
  const api = {
    async get(url) { const r = await fetch(url); if (!r.ok) throw new Error(r.status); return r.json(); },
    async send(method, url, body) {
      const r = await fetch(url, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(r.status);
      return r.json();
    },
  };

  function fmtBytes(n) {
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${u[i]}`;
  }
  function fmtTime(ts) { return new Date(ts * 1000).toLocaleString(); }
  function fmtDuration(s) {
    if (s == null) return "—";
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  async function loadAccessBanner() {
    const el = document.getElementById("access-banner");
    if (!el) return;
    try {
      const sys = await api.get("/api/system");
      const ts = sys.tailscale_running
        ? `Tailscale: <a href="${sys.access_url}">${sys.access_url}</a>`
        : `<span title="Tailscale not detected">Local only: <a href="${sys.local_url}">${sys.local_url}</a></span>`;
      el.innerHTML = `${ts} · ${fmtBytes(sys.media_bytes)} stored`;
    } catch (e) { /* ignore */ }
  }

  function badgeClass(status) {
    return status === "online" ? "online" : status === "degraded" ? "degraded" : "offline";
  }

  function initDashboard() {
    loadAccessBanner();
    const refresh = document.getElementById("refresh-cameras");
    refresh?.addEventListener("click", async () => {
      await api.send("POST", "/api/cameras/refresh");
      location.reload();
    });
    async function poll() {
      try {
        const cams = await api.get("/api/cameras");
        for (const cam of cams) {
          const tile = document.querySelector(`.tile[data-camera-id="${CSS.escape(cam.id)}"]`);
          const badge = tile?.querySelector("[data-status]");
          if (badge) {
            badge.className = "badge " + badgeClass(cam.status);
            badge.textContent = cam.status === "online" ? `${cam.fps.toFixed(0)} fps` : cam.status;
          }
        }
      } catch (e) { /* ignore */ }
    }
    poll();
    setInterval(poll, 3000);
  }

  function initCamera(cameraId) {
    loadAccessBanner();
    const enc = encodeURIComponent(cameraId);
    const feed = document.getElementById("feed");
    const controls = { fps: 15, zoom: 1, pan_x: 0.5, pan_y: 0.5 };

    function updateFeed() {
      const qs = `fps=${controls.fps}&zoom=${controls.zoom}&pan_x=${controls.pan_x}&pan_y=${controls.pan_y}`;
      feed.src = `/stream/${enc}.mjpg?${qs}&_t=${Date.now()}`;
    }
    function bindRange(id, key, fmt) {
      const input = document.getElementById(id);
      const out = document.getElementById(id + "-out");
      input?.addEventListener("input", () => {
        controls[key] = parseFloat(input.value);
        if (out) out.textContent = fmt ? fmt(controls[key]) : controls[key];
      });
      input?.addEventListener("change", updateFeed);
    }
    bindRange("fps", "fps", v => v.toFixed(0));
    bindRange("zoom", "zoom", v => v.toFixed(1) + "×");
    bindRange("pan_x", "pan_x");
    bindRange("pan_y", "pan_y");

    // Snapshot
    document.getElementById("snapshot-btn")?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      try { await api.send("POST", `/api/cameras/${enc}/snapshot`); e.target.textContent = "✓ Saved"; }
      catch { e.target.textContent = "✗ Failed"; }
      setTimeout(() => { e.target.textContent = "📸 Capture"; e.target.disabled = false; }, 1200);
    });

    // Recording
    let recording = false, recStart = 0, recInterval = null;
    const recBtn = document.getElementById("record-btn");
    const recInd = document.getElementById("rec-indicator");
    const recTimer = document.getElementById("rec-timer");
    recBtn?.addEventListener("click", async () => {
      if (!recording) {
        await api.send("POST", `/api/cameras/${enc}/recording/start`);
        recording = true; recStart = Date.now();
        recBtn.textContent = "⏹ Stop"; recBtn.classList.add("recording");
        recInd.classList.remove("hidden");
        recInterval = setInterval(() => {
          recTimer.textContent = fmtDuration((Date.now() - recStart) / 1000);
        }, 1000);
      } else {
        await api.send("POST", `/api/cameras/${enc}/recording/stop`);
        recording = false;
        recBtn.textContent = "⏺ Start"; recBtn.classList.remove("recording");
        recInd.classList.add("hidden"); clearInterval(recInterval);
      }
    });

    // Camera settings
    document.getElementById("apply-settings")?.addEventListener("click", async () => {
      const [w, h] = document.getElementById("resolution").value.split("x").map(Number);
      const body = {
        properties: {
          width: w, height: h,
          brightness: Number(document.getElementById("brightness").value),
          contrast: Number(document.getElementById("contrast").value),
        },
        transform: {
          rotation: Number(document.getElementById("rotation").value),
          flip_h: document.getElementById("flip_h").checked,
          flip_v: document.getElementById("flip_v").checked,
        },
      };
      await api.send("PATCH", `/api/cameras/${enc}`, body);
      updateFeed();
    });

    // Motion toggle
    document.getElementById("motion_enabled")?.addEventListener("change", async (e) => {
      await api.send("PATCH", `/api/cameras/${enc}`, { motion_enabled: e.target.checked });
    });

    // Rename
    document.getElementById("rename-btn")?.addEventListener("click", async () => {
      const name = document.getElementById("rename-input").value.trim();
      if (!name) return;
      await api.send("PATCH", `/api/cameras/${enc}`, { name });
      document.getElementById("camera-name").textContent = name;
    });

    // Status pill + initialise controls from server
    async function syncState() {
      try {
        const cam = await api.get(`/api/cameras/${enc}`);
        const pill = document.getElementById("status-pill");
        pill.className = "badge " + badgeClass(cam.status);
        pill.textContent = cam.status === "online" ? `${cam.fps.toFixed(0)} fps` : cam.status;
      } catch { /* ignore */ }
    }
    syncState();
    setInterval(syncState, 3000);
  }

  function initGallery() {
    loadAccessBanner();
    const grid = document.getElementById("media-grid");
    const empty = document.getElementById("media-empty");
    const camSel = document.getElementById("filter-camera");
    const typeSel = document.getElementById("filter-type");

    async function load() {
      const params = new URLSearchParams();
      if (camSel.value) params.set("camera_id", camSel.value);
      if (typeSel.value) params.set("media_type", typeSel.value);
      const items = await api.get(`/api/media?${params}`);
      grid.innerHTML = "";
      empty.classList.toggle("hidden", items.length > 0);
      for (const m of items) {
        const card = document.createElement("div");
        card.className = "media-card";
        const thumb = m.has_thumbnail ? `/media/${m.id}/thumbnail` : `/media/${m.id}/file`;
        const icon = m.media_type === "recording" ? "🎬" : "🖼";
        card.innerHTML = `
          <a href="/media/${m.id}/file" target="_blank"><img src="${thumb}" alt=""></a>
          <div class="media-meta"><span>${icon} ${fmtTime(m.created_ts)}</span><span>${fmtBytes(m.size_bytes)}</span></div>
          <div class="media-actions">
            <a class="btn ghost" href="/media/${m.id}/file" download>Download</a>
            <button class="btn ghost" data-del="${m.id}">Delete</button>
          </div>`;
        card.querySelector("[data-del]").addEventListener("click", async () => {
          await api.send("DELETE", `/api/media/${m.id}`);
          load();
        });
        grid.appendChild(card);
      }
    }
    camSel.addEventListener("change", load);
    typeSel.addEventListener("change", load);
    load();
  }

  function initEvents() {
    loadAccessBanner();
    const body = document.getElementById("events-body");
    const empty = document.getElementById("events-empty");
    const camSel = document.getElementById("filter-camera");
    async function load() {
      const params = new URLSearchParams();
      if (camSel.value) params.set("camera_id", camSel.value);
      const events = await api.get(`/api/events?${params}`);
      body.innerHTML = "";
      empty.classList.toggle("hidden", events.length > 0);
      for (const e of events) {
        const dur = e.end_ts ? e.end_ts - e.start_ts : null;
        const rec = e.recording_id
          ? `<a href="/media/${e.recording_id}/file" target="_blank">▶ View</a>` : "—";
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${fmtTime(e.start_ts)}</td><td>${e.camera_id}</td>
          <td>${fmtDuration(dur)}</td><td>${(e.peak_score * 100).toFixed(1)}%</td><td>${rec}</td>`;
        body.appendChild(tr);
      }
    }
    camSel.addEventListener("change", load);
    load();
    setInterval(load, 5000);
  }

  return { initDashboard, initCamera, initGallery, initEvents };
})();
