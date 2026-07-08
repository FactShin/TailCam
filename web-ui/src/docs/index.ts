// Documentation registry. Markdown ships inside the SPA via Vite `?raw` imports,
// so the whole wiki is served locally with TailCam — no network, no GitHub.

import overview from "./md/overview.md?raw";
import desktop from "./md/desktop.md?raw";
import installation from "./md/installation.md?raw";
import docker from "./md/docker.md?raw";
import quickstart from "./md/quickstart.md?raw";
import cameras from "./md/cameras.md?raw";
import motion from "./md/motion-detection.md?raw";
import recording from "./md/recording-media.md?raw";
import timelapse from "./md/timelapse.md?raw";
import ai from "./md/ai-analysis.md?raw";
import training from "./md/training.md?raw";
import activeLearning from "./md/active-learning.md?raw";
import homeAutomation from "./md/home-automation.md?raw";
import notifications from "./md/notifications.md?raw";
import plugins from "./md/plugins.md?raw";
import tailscale from "./md/tailscale.md?raw";
import fleet from "./md/fleet.md?raw";
import security from "./md/security.md?raw";
import mcpOverview from "./md/mcp-overview.md?raw";
import mcpConnect from "./md/mcp-connect.md?raw";
import mcpTools from "./md/mcp-tools.md?raw";
import mcpSecurity from "./md/mcp-security.md?raw";
import cli from "./md/cli.md?raw";
import configuration from "./md/configuration.md?raw";
import api from "./md/api.md?raw";
import troubleshooting from "./md/troubleshooting.md?raw";
import faq from "./md/faq.md?raw";

export interface DocPage {
  slug: string;
  title: string;
  group: string;
  summary: string;
  body: string;
}

export const DOCS: DocPage[] = [
  { slug: "overview", title: "Welcome", group: "Getting started", body: overview,
    summary: "What TailCam is and where to go next." },
  { slug: "desktop", title: "Desktop app", group: "Getting started", body: desktop,
    summary: "Menu-bar app for macOS (Linux/Windows next): dashboard window, service controls, fleet nodes." },
  { slug: "installation", title: "Installation", group: "Getting started", body: installation,
    summary: "Install TailCam, optional extras, and Tailscale." },
  { slug: "docker", title: "Running in Docker", group: "Getting started", body: docker,
    summary: "Run TailCam isolated in a container, with Tailscale." },
  { slug: "quickstart", title: "Quick start", group: "Getting started", body: quickstart,
    summary: "From install to a live, remotely-viewable camera." },

  { slug: "cameras", title: "Cameras", group: "Cameras & capture", body: cameras,
    summary: "Discovery, viewing, settings, transforms, hiding." },
  { slug: "motion-detection", title: "Motion detection", group: "Cameras & capture", body: motion,
    summary: "How motion works, tuning, and auto-record." },
  { slug: "recording-media", title: "Recording & media", group: "Cameras & capture", body: recording,
    summary: "Snapshots, recordings, the gallery, and retention." },
  { slug: "timelapse", title: "Timelapse", group: "Cameras & capture", body: timelapse,
    summary: "Capture, encode, smoothing, and print analysis." },

  { slug: "ai-analysis", title: "AI analysis", group: "Intelligence", body: ai,
    summary: "Label motion events locally with Ollama." },
  { slug: "training", title: "Training & models", group: "Intelligence", body: training,
    summary: "Datasets, training runs, and model lifecycle." },
  { slug: "active-learning", title: "Active learning", group: "Intelligence", body: activeLearning,
    summary: "Human-in-the-loop labeling with Label Studio, Florence-2, Qwen2.5-VL." },
  { slug: "notifications", title: "Notifications", group: "Intelligence", body: notifications,
    summary: "Discord / Telegram / webhook alerts for motion, offline, and training." },

  { slug: "home-automation", title: "Home automation", group: "Network & fleet", body: homeAutomation,
    summary: "Apple HomeKit cameras + Home Assistant (MJPEG cameras and MQTT)." },

  { slug: "tailscale", title: "Tailscale setup", group: "Network & fleet", body: tailscale,
    summary: "Serving over the tailnet, ports, and identity." },
  { slug: "fleet", title: "Fleet (multi-node)", group: "Network & fleet", body: fleet,
    summary: "Peer discovery, aggregated cameras, and relay." },
  { slug: "security", title: "Security & access", group: "Network & fleet", body: security,
    summary: "Principals, roles, grants, and the audit log." },

  { slug: "mcp-overview", title: "MCP overview", group: "Agents (MCP)", body: mcpOverview,
    summary: "Agent-ready control plane for Codex, Claude, Hermes, OpenClaw." },
  { slug: "mcp-connect", title: "Connecting agents", group: "Agents (MCP)", body: mcpConnect,
    summary: "Wire up stdio and remote MCP, with worked examples." },
  { slug: "mcp-tools", title: "Tools & resources", group: "Agents (MCP)", body: mcpTools,
    summary: "The full catalog of MCP tools, resources, and prompts." },
  { slug: "mcp-security", title: "MCP security", group: "Agents (MCP)", body: mcpSecurity,
    summary: "Transports, confirmation rules, and audit." },

  { slug: "cli", title: "CLI reference", group: "Reference", body: cli,
    summary: "Every tailcam command and environment variable." },
  { slug: "configuration", title: "Configuration", group: "Reference", body: configuration,
    summary: "Every config section and setting." },
  { slug: "plugins", title: "Plugins", group: "Reference", body: plugins,
    summary: "Extend TailCam with AI providers and notification channels." },
  { slug: "api", title: "API reference", group: "Reference", body: api,
    summary: "REST, streams, management, and MCP endpoints." },
  { slug: "troubleshooting", title: "Troubleshooting", group: "Reference", body: troubleshooting,
    summary: "Fixes for common problems." },
  { slug: "faq", title: "FAQ", group: "Reference", body: faq,
    summary: "Quick answers to common questions." },
];

export const DEFAULT_DOC = "overview";

export const DOC_ORDER: string[] = DOCS.map((d) => d.slug);

const BY_SLUG: Record<string, DocPage> = Object.fromEntries(DOCS.map((d) => [d.slug, d]));

export function getDoc(slug: string | undefined): DocPage | undefined {
  return BY_SLUG[slug ?? DEFAULT_DOC];
}

export interface DocGroup {
  group: string;
  docs: DocPage[];
}

export const DOC_GROUPS: DocGroup[] = DOCS.reduce<DocGroup[]>((acc, doc) => {
  const last = acc[acc.length - 1];
  if (last && last.group === doc.group) last.docs.push(doc);
  else acc.push({ group: doc.group, docs: [doc] });
  return acc;
}, []);

/** First non-heading paragraph of a doc, for search snippets. */
function firstParagraph(body: string): string {
  const lines = body.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].trim();
    if (l && !l.startsWith("#") && !l.startsWith("```")) return l;
  }
  return "";
}

export interface DocSearchHit {
  doc: DocPage;
  snippet: string;
}

/** Lightweight client-side search across titles, summaries, and body text. */
export function searchDocs(query: string): DocSearchHit[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const terms = q.split(/\s+/);
  const hits: { doc: DocPage; score: number; snippet: string }[] = [];
  for (const doc of DOCS) {
    const title = doc.title.toLowerCase();
    const summary = doc.summary.toLowerCase();
    const body = doc.body.toLowerCase();
    let score = 0;
    for (const t of terms) {
      if (title.includes(t)) score += 10;
      if (summary.includes(t)) score += 4;
      if (body.includes(t)) score += 1;
    }
    if (score === 0) continue;
    // Build a snippet around the first body match.
    let snippet = doc.summary;
    const idx = body.indexOf(terms[0]);
    if (idx >= 0) {
      const start = Math.max(0, idx - 40);
      snippet = (start > 0 ? "…" : "") + doc.body.slice(start, idx + 80).replace(/\n/g, " ").trim() + "…";
    } else if (!snippet) {
      snippet = firstParagraph(doc.body);
    }
    hits.push({ doc, score, snippet });
  }
  hits.sort((a, b) => b.score - a.score);
  return hits.slice(0, 12).map(({ doc, snippet }) => ({ doc, snippet }));
}
