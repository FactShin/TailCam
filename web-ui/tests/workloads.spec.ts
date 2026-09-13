import { createServer, type Server } from "node:http";
import { expect, test, type Page } from "@playwright/test";
import type { CameraInfo, DetectionResult } from "../src/types";
import { defaultWorkloadBudget } from "../src/components/WorkloadBudgetEditor";
import { WORKLOAD_TASKS, type DatasetRevision, type PlacementPolicyStatus, type SupervisionCreate, type SupervisionRecord, type WorkloadJob, type WorkloadProvider, type WorkloadWorker } from "../src/workloadTypes";

const LOCAL = "49ba90ab-a97a-4c6d-9ac8-1cfc2c48c7a9", REMOTE = "a9f3c6f2-3c4d-4cc9-8fbb-a36ab6e632f4";
const JOB = "262cbd4d-a861-4075-a8cc-416843b8629f", SECOND = "dd0da7b5-c3ad-4f1a-a717-b4d7cbb84110", SUPERVISION = "ac751f86-9925-4023-89cf-ac5f99be32d0";
const NOW = Math.floor(Date.now() / 1000);
const target = { node_id: REMOTE, provider_id: null, model: "registered-printer" };
const worker = (node = REMOTE): WorkloadWorker => ({ node_id: node, name: node === LOCAL ? "Camera Pi" : "GPU worker", online: true, roles: node === LOCAL ? ["capture"] : ["analysis", "training"], tasks: WORKLOAD_TASKS.map(task => ({ task, state: node === LOCAL ? "disabled" : "ready", code: node === LOCAL ? "role_disabled" : "ready", detail: node === LOCAL ? "This node has capture only." : "Runtime and capacity reported by this worker." })), queued: 0, running: 1, cpu_threads: 8, memory_bytes: 8 * 1024 ** 3, workspace_bytes: 4 * 1024 ** 3, gpu_slots: 1, latency_ms: 4 });
const job = (changes: Partial<WorkloadJob> = {}): WorkloadJob => ({ job_id: JOB, coordinator_node_id: LOCAL, origin_node_id: LOCAL, task: "training", state: "running", revision: 1, created_at: NOW - 60, updated_at: NOW - 2, deadline_at: NOW + 3600, started_at: NOW - 50, ended_at: null, requested_target: target, actual_target: target, priority: 20, cancel_requested: false, allowed_actions: ["cancel"], reference: {}, stages: [{ stage_id: "train", task: "training", state: "running", attempt: 1, worker_node_id: REMOTE, placement_plan: { task: "training", mode: "manual", policy_revision: 3, requested_target: target, selected_target: target, fallback_targets: [], reason: "Manual worker selection", budget: defaultWorkloadBudget() }, progress: { fraction: 0.4, epoch: 4, message: "Epoch in progress", metrics: {} }, heartbeat_at: NOW - 2, started_at: NOW - 50, ended_at: null, error: null, outputs: [], result: {} }], error: null, ...changes });
const approved = (): SupervisionCreate => ({ objective: { name: "Improve printer detector", task: "detection", dataset_id: 7, dataset_revision: "a".repeat(64), camera_ids: ["/dev/video0"], classes: ["printer", "failure"], success_criteria: [{ metric: "map50", direction: "maximize", threshold: 0.8 }], evaluation_reference: "reviewed-evaluation-split" }, policy: { allowed_model_ids: [4], allowed_worker_node_ids: [REMOTE], epochs: { minimum: 1, maximum: 10 }, image_size: { minimum: 224, maximum: 640 }, allowed_seeds: [0, 1], max_experiments: 3, total_wall_seconds: 10800, experiment_budget: defaultWorkloadBudget(), max_attempts: 1, agent_timeout_seconds: 300, permitted_actions: ["experiment", "inspect", "stop", "finish"], activation_enabled: false } });
const supervision = (changes: Partial<SupervisionRecord> = {}): SupervisionRecord => ({ supervision_id: SUPERVISION, node_id: LOCAL, owner: "verified-admin", revision: 1, ...approved(), created_at: NOW - 60, updated_at: NOW - 60, state: "active", agent_session_id: null, agent_heartbeat_at: null, agent_connected: false, last_decision: "approved", reason: "Awaiting an experiment within the approved policy", next_check_at: null, current_job_id: null, remaining_experiments: 3, remaining_wall_seconds: 10800, experiments: [], allowed_actions: ["experiment", "inspect", "stop", "finish"], ...changes });

let server: Server;
let policy: PlacementPolicyStatus; let workers: WorkloadWorker[]; let providers: WorkloadProvider[];
let jobs: WorkloadJob[]; let records: SupervisionRecord[]; let revision: DatasetRevision;
let policyStatus: number; let policySaveStatus: number; let jobStatus: number; let providerStatus: number;
let settingsStatus: number;
let modelsHaveWeights: boolean;
let visibleCameras: CameraInfo[]; let cameraDetection: DetectionResult;
let approvalStatus: number; let experimentStatus: number; let roles: string[]; let paginated: boolean;
const requests: { url: string; method: string; body: any }[] = [];

test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    const url = new URL(req.url!, "http://127.0.0.1:4174"), path = url.pathname, method = req.method!;
    let raw = ""; for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : undefined;
    requests.push({ url: req.url!, method, body });
    res.setHeader("Content-Type", "application/json"); res.setHeader("Cache-Control", "no-store");
    const send = (value: unknown, status = 200) => res.writeHead(status).end(JSON.stringify(value));
    if (path === "/api/notifications" || path === "/api/integrations") return send({ detail: settingsStatus === 403 ? "admin role required" : "settings unavailable" }, settingsStatus);
    if (path === "/api/cameras/fixture-camera/detect") return send(cameraDetection);
    if (path.startsWith("/stream/")) { res.setHeader("Content-Type", "image/png"); return res.end(Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64")); }
    if (path === "/api/v1/workloads/policy") {
      if (method === "PATCH") {
        if (policySaveStatus !== 200 || body.expected_revision !== policy.policy.revision) return send({ detail: "Placement policy changed; reload before saving." }, policySaveStatus !== 200 ? policySaveStatus : 409);
        policy = { ...policy, policy: { ...body.policy, revision: policy.policy.revision + 1 } }; return send(policy);
      }
      return send(policyStatus === 200 ? policy : { detail: "Workload placement unavailable" }, policyStatus);
    }
    if (path === "/api/v1/workloads/workers") return send({ items: workers });
    if (path === "/api/v1/workloads/providers") {
      if (method === "POST") { if (providerStatus !== 200) return send({ detail: "Invalid model endpoint" }, providerStatus); providers = [...providers.filter(item => item.provider_id !== body.provider_id), body]; return send(body); }
      return send({ items: providers });
    }
    if (path === "/api/v1/jobs") {
      if (jobStatus !== 200) return send({ detail: "Job coordinator unavailable" }, jobStatus);
      const items = jobs.filter(item => (!url.searchParams.get("task") || item.task === url.searchParams.get("task")) && (!url.searchParams.get("state") || item.state === url.searchParams.get("state")));
      return send({ items: url.searchParams.has("cursor") ? [job({ job_id: SECOND, state: "succeeded", allowed_actions: [] })] : items, next_cursor: paginated && !url.searchParams.has("cursor") ? "50" : null });
    }
    if (path.startsWith("/api/v1/jobs/")) {
      const id = path.split("/")[4]; const current = jobs.find(item => item.job_id === id);
      if (!current) return send({ detail: "Job not found" }, 404);
      if (method === "POST") { current.state = path.endsWith("/cancel") ? "cancel_requested" : "queued"; current.cancel_requested = path.endsWith("/cancel"); current.allowed_actions = current.cancel_requested ? [] : ["cancel"]; current.revision++; }
      return send(current);
    }
    if (path === "/api/v1/training/datasets/7/revision") return send(revision);
    if (path === "/api/v1/training/supervisions") {
      if (method === "POST") {
        if (approvalStatus !== 200) return send({ detail: "Review the current dataset revision" }, approvalStatus);
        const created = supervision(body); records = [created]; return send(created);
      }
      return send({ items: records, next_cursor: null });
    }
    if (path.startsWith("/api/v1/training/supervisions/")) {
      const id = path.split("/")[5]; const current = records.find(item => item.supervision_id === id);
      if (!current) return send({ detail: "Supervision not found" }, 404);
      if (path.endsWith("/experiments")) {
        if (experimentStatus !== 200) return send({ detail: "Worker admission could not be confirmed" }, experimentStatus);
        current.remaining_experiments--; current.remaining_wall_seconds -= 3600; current.current_job_id = JOB;
        current.allowed_actions = ["inspect", "stop", "finish"]; current.last_decision = "experiment";
        current.experiments.push({ experiment_id: SECOND, job_id: JOB, request: body, created_at: NOW - 59, reserved_wall_seconds: 3600, state: "running", job: job({ reference: { supervision_id: SUPERVISION } }), error_code: null }); return send(current);
      }
      if (path.endsWith("/stop")) { current.state = "stop_requested"; current.allowed_actions = ["inspect", "stop"]; return send(current); }
      if (path.endsWith("/finish")) { current.state = "completed"; current.allowed_actions = ["inspect"]; return send(current); }
      if (path.endsWith("/report")) return send({ supervision: current, comparison: current.experiments.map(item => ({ experiment_id: item.experiment_id, job_id: item.job_id, state: "succeeded", parameters: item.request, dataset_id: 7, dataset_revision: current.objective.dataset_revision, metrics: { map50: 0.71 }, artifacts: [{ artifact_id: SECOND, owner_node_id: REMOTE, size_bytes: 128, sha256: "b".repeat(64), slot: "model" }], provenance: [{ stage_id: "train", worker_node_id: REMOTE, placement_plan: null, started_at: NOW - 60, ended_at: NOW - 50, result: { runtime_version: "fixture", device: "synthetic" } }], reserved_wall_seconds: 3600, error_code: null })), best_candidate: current.experiments[0]?.experiment_id ?? null, comparison_metric: "map50", activation_performed: false, limitations: ["Candidate ranking uses reported training metrics, not a validated promotion gate.", "The evaluation reference is recorded; held-out evaluation is not executed here."] });
      return send(current);
    }
    if (path === "/api/training/runs") return send(method === "POST" ? { id: 12, dataset_id: body.dataset_id, model_id: null, base_model: body.base_model, status: "queued", epochs: body.epochs, epoch: 0, metrics: {}, log: "Queued", created_ts: NOW, started_ts: null, ended_ts: null } : []);
    const fixtures: Record<string, unknown> = {
      "/api/ai": { enabled: false, pipeline: null },
      "/api/training": { engine_available: false, framework: "ultralytics", version: null, device: "cpu", collecting: false, collect_enabled: false, collect_interval_seconds: 30, auto_label: false, active_dataset_id: 7, active_model_id: 4, classes: ["printer", "failure"], total_samples: 30, dataset_count: 1, model_count: 1, collected_session: 0 },
      "/api/datasets/7/samples": [],
      "/api/cameras": visibleCameras,
      "/api/detection": { enabled: true, engine: "fixture", model: "configured-default", status: "ready", percent: 100, detail: "", error: "", confidence: 0.5, classes: [], overlay_default: false, node: "gpu", node_reachable: true, node_error: "", low_power_host: false }, "/api/update": { current: "1.11.0", latest: "1.11.0", available: false },
      "/api/system": { version: "1.11.0", host: "camera-pi", node_id: LOCAL, node_name: "Camera Pi", node_roles: ["capture"], tailscale_installed: false, tailscale_running: false, access_url: "http://localhost:4173", local_url: "http://localhost:4173", media_bytes: 0, hidden_count: 0, host_model: "Isolated fixture", ram_gb: 8, cpu_count: 4, low_power: false },
      "/api/hosts": [LOCAL, REMOTE].map(id => ({ host: id === LOCAL ? "camera-pi" : "gpu-worker", node_key: id === LOCAL ? "local" : "gpu", kind: id === LOCAL ? "local" : "peer", online: true, version: "1.11.0", camera_count: 0, proxy_prefix: id === LOCAL ? "" : "/proxy/gpu", node_id: id, node_name: id === LOCAL ? "Camera Pi" : "GPU worker", node_roles: id === LOCAL ? ["capture"] : ["training", "analysis"] })),
      "/api/v1/node/capabilities": { api_version: "1", capabilities: [], actions: [], principal: { actor: "fixture", display_name: "Fixture", source: "local", verified: true, roles } },
      "/api/datasets": [{ id: 7, name: "Reviewed printer examples", task: "detection", sample_count: 30, annotated_count: 30, created_ts: 1, note: "", label_counts: {}, box_label_counts: { printer: 20, failure: 10 } }],
      "/api/models": [{ id: 4, name: "Printer baseline", kind: "byo", task: "detection", active: true, base_model: "fixture", classes: ["printer", "failure"], metrics: {}, created_ts: 1, has_artifact: modelsHaveWeights }, { id: 1, name: "Unprovisioned default", kind: "base", task: "detection", active: false, base_model: "yolo11n.pt", classes: [], metrics: {}, created_ts: 1, has_artifact: false }, { id: 2, name: "Classification model", kind: "byo", task: "classification", active: false, base_model: "fixture", classes: [], metrics: {}, created_ts: 1, has_artifact: true }],
    };
    return path in fixtures ? send(fixtures[path]) : send({ detail: "Unavailable in isolated workload fixture" }, 404);
  });
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(4174, "127.0.0.1", resolve); });
});
test.afterAll(async () => { await new Promise<void>((resolve, reject) => { server.close(error => error ? reject(error) : resolve()); server.closeAllConnections(); }); });
test.beforeEach(async ({ page }) => {
  policy = { source_node_id: LOCAL, policy: { revision: 1, routes: {} } }; workers = [worker(LOCAL), worker()]; providers = [];
  visibleCameras = []; cameraDetection = { camera_id: "fixture-camera", detector_active: true, model_name: "actual-worker-model", boxes: [], workload: { worker_node_id: REMOTE, model_name: "actual-worker-model", session_id: SECOND, execution_ms: 12.5, queue_ms: 0, round_trip_ms: 24 } };
  jobs = [job()]; records = [supervision()]; revision = { dataset_id: 7, revision: "a".repeat(64), task: "detection", camera_ids: ["/dev/video0"], classes: ["printer", "failure"] };
  policyStatus = policySaveStatus = jobStatus = providerStatus = approvalStatus = experimentStatus = 200; roles = ["admin", "operator", "viewer"]; paginated = false; modelsHaveWeights = true; requests.length = 0;
  settingsStatus = 404;
  await page.addInitScript(() => sessionStorage.setItem("tailcam.booted", "1"));
});
const posts = (suffix: string) => requests.filter(item => item.method === "POST" && item.url.endsWith(suffix));

test("restricted Settings never renders empty editable administrative forms", async ({ page }) => {
  roles = ["operator", "viewer"]; settingsStatus = 403;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");
  const notifications = page.locator(".notif-panel").filter({ hasText: "Notifications" });
  const integrations = page.locator(".notif-panel").filter({ hasText: "Home automation" });
  await expect(notifications.getByRole("alert")).toHaveText("Administrator access is required to view notification settings.");
  await expect(integrations.getByRole("alert")).toHaveText("Administrator access is required to view integration settings and pairing details.");
  await expect(page.getByRole("switch", { name: "Enable notifications" })).toHaveCount(0);
  await expect(notifications.locator("input")).toHaveCount(0);
  await expect(integrations.locator("input")).toHaveCount(0);
  await expect(notifications).not.toContainText("No channels set up yet");
  settingsStatus = 503;
  await notifications.getByRole("button", { name: "Try again" }).click();
  await integrations.getByRole("button", { name: "Try again" }).click();
  await expect(notifications.getByRole("alert")).toHaveText("Notification settings could not be loaded.");
  await expect(integrations.getByRole("alert")).toHaveText("Integration settings could not be loaded.");
  expect(requests.filter(item => item.method !== "GET" && ["/api/notifications", "/api/integrations"].includes(item.url))).toHaveLength(0);
});

async function openApproval(page: Page) {
  await page.goto("/workloads?tab=supervisor"); await page.getByRole("button", { name: "Approve a new objective" }).click();
  await page.getByLabel("Training objective name").fill("Improve reviewed printer detection");
  await page.getByLabel("Supervisor dataset").selectOption("7");
  await page.getByRole("button", { name: "Review current dataset revision" }).click();
  await expect(page.getByText("Reviewed detection dataset #7", { exact: false })).toBeVisible();
  await page.getByLabel("Evaluation reference", { exact: true }).fill("reviewed-split-v1");
  await page.getByLabel("Success metric", { exact: true }).fill("map50");
  await page.getByLabel("Target metric value").fill("0.8");
  await page.getByRole("checkbox", { name: "Printer baseline · #4" }).check();
  await page.getByRole("checkbox", { name: "GPU worker · ready", exact: true }).check();
}

test("mobile placement saves explicit target without implicit fallbacks or model calls", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/workloads");
  expect((await page.getByRole("navigation", { name: "Workload views" }).getByRole("button", { name: "Placement", exact: true }).boundingBox())!.height).toBeGreaterThanOrEqual(40);
  await page.getByRole("switch", { name: "Configure placement for this task" }).click();
  await page.getByLabel("Requested worker", { exact: true }).selectOption(`node:${REMOTE}`);
  expect(requests.filter(item => item.method === "PATCH")).toHaveLength(0);
  await page.getByRole("button", { name: "Save placement" }).click();
  await expect(page.getByText("Placement saved for new work.")).toBeVisible();
  const body = requests.find(item => item.method === "PATCH")!.body;
  expect(body.expected_revision).toBe(1); expect(body.policy.routes.live_detection.target.node_id).toBe(REMOTE);
  expect(body.policy.routes.live_detection.fallback_targets).toEqual([]);
  expect(requests.some(item => item.url === "/api/ai" || item.url.includes("probe=true"))).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth && document.querySelector(".content")!.scrollWidth <= document.querySelector(".content")!.clientWidth)).toBe(true);
  await page.getByRole("heading", { name: "Workloads", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("workloads-placement-mobile.png"), fullPage: true });
});
test("placement revision conflict preserves edits and explicit reload fetches saved policy", async ({ page }) => {
  await page.goto("/workloads"); await page.getByRole("switch", { name: "Configure placement for this task" }).click();
  await page.getByLabel("Requested worker", { exact: true }).selectOption(`node:${REMOTE}`);
  policy.policy.revision = 2; await page.getByRole("button", { name: "Save placement" }).click();
  await expect(page.getByRole("alert")).toContainText("Placement policy changed");
  await expect(page.getByLabel("Requested worker", { exact: true })).toHaveValue(`node:${REMOTE}`);
  await page.getByRole("button", { name: "Reload saved placement" }).click();
  await expect(page.getByRole("switch", { name: "Configure placement for this task" })).not.toBeChecked();
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();
});
test("placement rejects a CPU allowance below its thread and wall reservation", async ({ page }) => {
  await page.goto("/workloads");
  await page.getByRole("switch", { name: "Configure placement for this task" }).click();
  await page.getByLabel("Requested worker", { exact: true }).selectOption(`node:${REMOTE}`);
  await page.getByLabel("CPU threads", { exact: false }).fill("2");
  await expect(page.getByText("CPU allowance must cover CPU threads", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save placement" })).toBeDisabled();
  expect(requests.filter(item => item.method === "PATCH")).toHaveLength(0);
  await page.getByLabel("CPU allowance (thread-seconds)", { exact: false }).fill("7200");
  await page.getByRole("button", { name: "Save placement" }).click();
  await expect(page.getByText("Placement saved for new work.")).toBeVisible();
  expect(requests.find(item => item.method === "PATCH")!.body.policy.routes.live_detection.budget.cpu_seconds).toBe(7200);
});
test("Auto requires an explicit approved list and never treats unchecked as ready", async ({ page }) => {
  workers[1].tasks.forEach(task => { task.state = "unchecked"; task.detail = "Runtime not exercised."; });
  await page.goto("/workloads"); await page.getByRole("switch", { name: "Configure placement for this task" }).click();
  await page.getByLabel("Worker selection mode").selectOption("auto");
  await expect(page.getByRole("button", { name: "Save placement" })).toBeDisabled();
  await page.getByRole("checkbox", { name: "GPU worker · unchecked" }).check();
  await page.getByRole("button", { name: "Save placement" }).click();
  expect(requests.find(item => item.method === "PATCH")!.body.policy.routes.live_detection.approved_node_ids).toEqual([REMOTE]);
  await expect(page.getByText("Unchecked and offline workers remain ineligible", { exact: false })).toBeVisible();
});
test("endpoint validation preserves entered configuration and repeated registration identity", async ({ page }) => {
  providerStatus = 422; await page.goto("/workloads"); await page.getByText("Register a model endpoint", { exact: true }).click();
  await page.getByLabel("Endpoint name").fill("Local vision server"); await page.getByLabel("Endpoint base URL").fill("http://models:11434"); await page.getByLabel("Endpoint model", { exact: true }).fill("vision-fixture");
  await page.getByRole("button", { name: "Register endpoint", exact: true }).click(); await expect(page.getByRole("alert")).toContainText("Invalid model endpoint");
  providerStatus = 200; await page.getByRole("button", { name: "Register endpoint", exact: true }).click();
  await expect(page.getByText("Model endpoint registered.")).toBeVisible();
  const submitted = posts("/providers"); expect(submitted[0].body.provider_id).toBe(submitted[1].body.provider_id);
});
test("job cancellation stays requested until worker confirmation", async ({ page }) => {
  await page.goto("/workloads?tab=jobs"); await page.getByRole("button", { name: "Request stop", exact: true }).click();
  await expect(page.getByText("Cancel requested", { exact: true })).toBeVisible();
  await expect(page.getByText("Cancellation is requested. Work has not yet confirmed it stopped.")).toBeVisible();
  expect(posts(`/${JOB}/cancel`)).toHaveLength(1);
  jobs[0].state = "cancelled"; jobs[0].cancel_requested = false; jobs[0].stages[0].state = "cancelled";
  await page.getByRole("button", { name: "Refresh jobs" }).click(); await expect(page.getByText("Stopped", { exact: true }).first()).toBeVisible();
});
test("single job deep link stays bound to its coordinator job and retry obeys permissions", async ({ page }) => {
  roles = ["operator", "viewer"]; jobs = [job({ state: "failed", allowed_actions: ["retry"] })];
  await page.goto(`/workloads?tab=jobs&job=${JOB}`); await expect(page.getByText("Selected worker job")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry permitted work" })).toHaveCount(0);
  expect(requests.some(item => item.url === `/api/v1/jobs/${JOB}`)).toBe(true);
  expect(requests.some(item => item.url.startsWith("/api/v1/jobs?"))).toBe(false);
});
test("job filters and pagination retain explicit failed and unavailable outcomes", async ({ page }) => {
  paginated = true; jobs = [job({ task: "live_detection", state: "failed", allowed_actions: ["retry"], error: { code: "inference_unavailable", detail: "Inference engine is unavailable.", retryable: true } })];
  await page.goto("/workloads?tab=jobs"); await page.getByLabel("Filter jobs by task").selectOption("live_detection");
  await expect(page.getByText("Inference engine is unavailable.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Load more jobs" }).click();
  await expect.poll(() => requests.some(item => item.url.includes("task=live_detection") && item.url.includes("cursor=50"))).toBe(true);
});
test("successful empty detection is distinct from missing live metrics", async ({ page }) => {
  const item = job({ task: "live_detection", state: "succeeded", allowed_actions: [] });
  item.stages[0] = { ...item.stages[0], task: "live_detection", state: "succeeded", result: { available: true, outcome: "succeeded", predictions: [{ slot: "frame", boxes: [] }] } }; jobs = [item];
  await page.goto("/workloads?tab=jobs"); await expect(page.getByText("Inference completed successfully with no detections.")).toBeVisible();
  await expect(page.getByText("Metrics not reported.")).toBeVisible();
});
test("approval freezes the reviewed dataset hash and never starts an experiment", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 }); await openApproval(page);
  const approve = page.getByRole("button", { name: "Approve experiment policy" }); await expect(approve).toBeDisabled();
  await page.getByRole("checkbox", { name: "I approve this dataset revision", exact: false }).check();
  await approve.click(); await expect(page.getByText("Improve reviewed printer detection", { exact: true })).toBeVisible();
  const body = posts("/training/supervisions")[0].body;
  expect(body.objective.dataset_revision).toBe("a".repeat(64)); expect(body.policy.activation_enabled).toBe(false);
  expect(body.policy.allowed_worker_node_ids).toEqual([REMOTE]); expect(posts("/experiments")).toHaveLength(0);
  expect(requests.some(item => item.url.endsWith("/heartbeat") || item.url.includes("/activate"))).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth && document.querySelector(".content")!.scrollWidth <= document.querySelector(".content")!.clientWidth)).toBe(true);
  await page.getByText("External supervisor", { exact: true }).scrollIntoViewIfNeeded();
  await page.getByRole("heading", { name: "Improve reviewed printer detection", exact: true }).evaluate(element => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: info.outputPath("supervision-approved-mobile.png") });
});
test("changing a reviewed resource requirement clears approval acknowledgment", async ({ page }) => {
  await openApproval(page); const acknowledgement = page.getByRole("checkbox", { name: "I approve this dataset revision", exact: false });
  await acknowledgement.check(); await page.getByRole("switch", { name: "Per-experiment resource limits: Require hard memory limit" }).click();
  await expect(acknowledgement).not.toBeChecked(); await expect(page.getByRole("button", { name: "Approve experiment policy" })).toBeDisabled();
});
test("rejected stale-dataset approval preserves draft until a fresh review", async ({ page }) => {
  await openApproval(page); approvalStatus = 409;
  await page.getByRole("checkbox", { name: "I approve this dataset revision", exact: false }).check(); await page.getByRole("button", { name: "Approve experiment policy" }).click();
  await expect(page.getByRole("alert")).toContainText("Review the current dataset revision");
  await expect(page.getByLabel("Training objective name")).toHaveValue("Improve reviewed printer detection");
  revision.revision = "c".repeat(64); approvalStatus = 200;
  await page.getByRole("button", { name: "Review current dataset revision" }).click();
  await page.getByRole("checkbox", { name: "I approve this dataset revision", exact: false }).check(); await page.getByRole("button", { name: "Approve experiment policy" }).click();
  await expect.poll(() => posts("/training/supervisions").length).toBe(2);
  expect(posts("/training/supervisions")[1].body.objective.dataset_revision).toBe("c".repeat(64));
});
test("unconfirmed experiment retries reuse the same idempotency key", async ({ page }) => {
  experimentStatus = 503; await page.goto(`/workloads?tab=supervisor&supervision=${SUPERVISION}`);
  await page.getByLabel("Experiment reason").fill("Compare one permitted seed on reviewed examples");
  await page.getByRole("button", { name: "Submit approved experiment" }).click();
  await expect(page.getByRole("alert")).toContainText("submission key is retained");
  experimentStatus = 200; await page.getByRole("button", { name: "Submit approved experiment" }).click();
  await expect(page.getByText("2 of 3", { exact: true })).toBeVisible();
  expect(posts("/experiments")[0].body.idempotency_key).toBe(posts("/experiments")[1].body.idempotency_key);
  expect(posts("/experiments")[1].body.worker_node_id).toBe(REMOTE);
});
test("supervisor connectivity is independent from worker progress and final evidence", async ({ page }, info) => {
  const record = supervision({ current_job_id: JOB, remaining_experiments: 2, remaining_wall_seconds: 7200, allowed_actions: ["inspect", "stop", "finish"] });
  record.experiments = [{ experiment_id: SECOND, job_id: JOB, request: { idempotency_key: "fixture", base_model_id: 4, worker_node_id: REMOTE, epochs: 5, image_size: 320, seed: 0, reason: "Reviewed comparison" }, created_at: NOW - 60, reserved_wall_seconds: 3600, state: "running", job: job({ reference: { supervision_id: SUPERVISION } }), error_code: null }]; records = [record];
  await page.goto(`/workloads?tab=supervisor&supervision=${SUPERVISION}`);
  await expect(page.getByText("Disconnected", { exact: true })).toBeVisible(); await page.getByText("Printer baseline · 5 epochs · running", { exact: true }).click();
  await expect(page.getByText("Last heartbeat:", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Open comparison report" }).click();
  await expect(page.getByText("held-out evaluation is not executed here", { exact: false })).toBeVisible();
  await expect(page.getByText("map50: 0.71", { exact: true })).toBeVisible();
  expect(requests.some(item => item.url.endsWith("/heartbeat") || item.url.includes("/activate"))).toBe(false);
  await expect(page.getByText("Highest-ranked reported candidate: Printer baseline", { exact: false })).toBeVisible();
  await page.getByRole("heading", { name: "Evidence and comparison" }).scrollIntoViewIfNeeded();
  await page.locator(".panel").filter({ has: page.getByRole("heading", { name: "Evidence and comparison" }) }).screenshot({ path: info.outputPath("supervision-evidence-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("heading", { name: "Evidence and comparison" }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth && document.querySelector(".content")!.scrollWidth <= document.querySelector(".content")!.clientWidth)).toBe(true);
  await page.locator(".panel").filter({ has: page.getByRole("heading", { name: "Evidence and comparison" }) }).screenshot({ path: info.outputPath("supervision-evidence-mobile.png") });
});
test("supervision stop remains requested and hides experiment submission", async ({ page }) => {
  await page.goto(`/workloads?tab=supervisor&supervision=${SUPERVISION}`);
  await page.getByRole("button", { name: "Request supervision stop" }).click();
  await expect(page.getByText("Stop requested. Wait for the worker's terminal state", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit approved experiment" })).toHaveCount(0);
  expect(posts("/stop")).toHaveLength(1);
});
test("viewer sees progress but no approval, experiment, stop or retry actions", async ({ page }) => {
  roles = ["viewer"]; await page.goto(`/workloads?tab=supervisor&supervision=${SUPERVISION}`);
  await expect(page.getByText("Improve printer detector", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve a new objective" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit approved experiment" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Request supervision stop" })).toHaveCount(0);
  expect(requests.some(item => item.method !== "GET")).toBe(false);
});
test("job read failure retains the last snapshot with a stale notice and recovers", async ({ page }) => {
  await page.goto("/workloads?tab=jobs"); await expect(page.getByText("Epoch in progress", { exact: false })).toBeVisible();
  jobStatus = 503; await page.getByRole("button", { name: "Refresh jobs" }).click();
  await expect(page.getByRole("alert")).toContainText("displayed jobs may be stale");
  await expect(page.getByText("Epoch in progress", { exact: false })).toBeVisible();
  jobStatus = 200; await page.getByRole("button", { name: "Retry loading jobs" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});


test("manual training chooses registered task-matching weights even without a local training package", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/ai?tab=training");
  const train = page.getByRole("button", { name: "Train", exact: true });
  await expect(train).toBeDisabled();
  const model = page.getByLabel("Registered training base model");
  await expect(model.locator("option")).toHaveText(["Choose registered weights", "Printer baseline · #4"]);
  await model.selectOption("4");
  await expect(page.getByText("Local training package unavailable")).toBeVisible();
  await expect(train).toBeEnabled(); await train.click();
  await expect.poll(() => posts("/training/runs").length).toBe(1);
  expect(posts("/training/runs")[0].body).toEqual({ dataset_id: 7, epochs: 30, base_model: "model:4" });
  expect(requests.some(item => item.url.includes("/activate"))).toBe(false);
  await model.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth && document.querySelector(".content")!.scrollWidth <= document.querySelector(".content")!.clientWidth)).toBe(true);
  await page.locator(".manual-training-panel").screenshot({ path: info.outputPath("manual-training-mobile.png") });
});
test("manual training with no stored weights directs model import and cannot send a default", async ({ page }) => {
  modelsHaveWeights = false; await page.goto("/ai?tab=training");
  await expect(page.getByText("No registered detection model reports stored weights.", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "Import an existing .pt model in Models" })).toHaveAttribute("href", "/ai?tab=models");
  await expect(page.getByRole("button", { name: "Train", exact: true })).toBeDisabled();
  expect(posts("/training/runs")).toHaveLength(0);
});


test("camera execution details observe actual worker timings without starting inference and retain legacy unknowns", async ({ page }, info) => {
  visibleCameras = [{ id: "fixture-camera", name: "Isolated camera fixture", backend: "synthetic", status: "online", fps: 10, width: 640, height: 480, recording: false, motion_enabled: false, detection_enabled: true, detection_override: null, properties: {}, transform: { rotation: 0, flip_h: false, flip_v: false }, stream: { fps: 10, quality: 70, max_width: 640 }, stream_overrides: { fps: null, quality: null, max_width: null }, last_error: null, host: "camera-pi", proxy_prefix: "" }];
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/camera/camera-pi/fixture-camera");
  const details = page.locator(".detection-execution");
  await details.locator("summary").first().click();
  await expect(details).toContainText("No detection result received yet.");
  expect(posts("/detect")).toHaveLength(0);
  await page.getByRole("button", { name: "Toggle object detection", exact: true }).click();
  await expect(details).toContainText("actual-worker-model");
  await expect(details).toContainText("GPU worker");
  await expect(details).toContainText("12.5 ms"); await expect(details).toContainText("0 ms");
  await expect(details).not.toContainText("configured-default");
  await details.locator("summary").first().click(); await details.locator("summary").first().click();
  expect(posts("/detect")).toHaveLength(1);
  await details.screenshot({ path: info.outputPath("camera-execution-mobile.png") });
  await page.getByRole("button", { name: "Toggle object detection", exact: true }).click();
  await expect(details).toContainText("The detection overlay is paused or off.");
  cameraDetection = { camera_id: "fixture-camera", detector_active: true, model_name: "legacy-reported-model", boxes: [] };
  await page.getByRole("button", { name: "Toggle object detection", exact: true }).click();
  await expect(details).toContainText("This node did not report worker or timing observations.");
  await expect(details).toContainText("legacy-reported-model");
  await expect(details).not.toContainText("12.5 ms");
  await expect(details).not.toContainText("GPU worker");
});
