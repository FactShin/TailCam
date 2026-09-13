import { createServer, type Server } from "node:http";
import { expect, test } from "@playwright/test";
import type { MigrationInput, MigrationPreview, StorageArtifact, StorageLocation, StorageMigration, StoragePolicyStatus, StorageTransfer } from "../src/storageTypes";

const LOCAL = "49ba90ab-a97a-4c6d-9ac8-1cfc2c48c7a9";
const REMOTE = "a9f3c6f2-3c4d-4cc9-8fbb-a36ab6e632f4";
const ROOT = "dd0da7b5-c3ad-4f1a-a717-b4d7cbb84110";
const ARCHIVE = "09541275-e70b-4c8a-8f6a-19536de909d1";
const ARTIFACT = "262cbd4d-a861-4075-a8cc-416843b8629f";
const PREVIEW = "ac751f86-9925-4023-89cf-ac5f99be32d0";
const GIB = 1024 ** 3;
const location = (id = ROOT, node = LOCAL): StorageLocation => ({location_id: id, node_id: node, label: id === ROOT ? "Camera disk" : "Archive disk", path: "/fixture/media", marker: id, device: 1, inode: 2, created_at: 1700000000, quota_bytes: 50 * GIB, reserve_bytes: 2 * GIB, is_default: true, state: "ready", used_bytes: 5 * GIB, reserved_bytes: GIB, free_bytes: 20 * GIB, allocatable_bytes: 18 * GIB});
const artifact = (overrides: Partial<StorageArtifact> = {}): StorageArtifact => ({artifact_id: ARTIFACT, owner_node_id: REMOTE, origin_node_id: LOCAL, camera_id: "/dev/video0", kind: "recording", mime_type: "video/mp4", size_bytes: 100, sha256: "a".repeat(64), created_at: 1700000000, updated_at: 1700000000, state: "committed", location_id: ARCHIVE, requested_destination: {node_id: REMOTE, location_id: ARCHIVE}, policy_revision: 1, parent_id: null, metadata: {name: "Workshop clip", url: "https://untrusted.invalid/file"}, retention: {enabled: false, max_age_seconds: 0, min_replicas: 1, protect: false}, replicas: [], owner_online: false, last_seen: 1700000000, ...overrides});
let backend: Server;
let policy: StoragePolicyStatus;
let localLocations: StorageLocation[];
let policyReadStatus: number;
let policyWriteStatus: number;
let catalogStatus: number;
let catalog: StorageArtifact[];
let transfers: StorageTransfer[];
let jobs: StorageMigration[];
let previewResult: MigrationPreview | undefined;
let previewBlocked: boolean;
let expiredPreview: boolean;
let admissionsAllowed: boolean;
let locationStatus: number;
let retentionStatus: number;
let principalRoles: string[];
const requests: {url: string; method: string; body: any}[] = [];

test.beforeAll(async () => {
  backend = createServer(async (req, res) => {
    const url = new URL(req.url!, "http://127.0.0.1:4174");
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : undefined;
    const method = req.method!;
    requests.push({url: req.url!, method, body});
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", "no-store");
    const send = (data: unknown, status = 200) => res.writeHead(status).end(JSON.stringify(data));
    const path = url.pathname;
    if (path === "/api/v1/storage/policy") {
      if (method === "PATCH") {
        if (policyWriteStatus !== 200) return send({detail: "Policy revision changed"}, policyWriteStatus);
        if (body.expected_revision !== policy.policy.revision) return send({detail: "Policy revision changed"}, 409);
        policy = {...policy, enabled: true, policy: {...body.policy, revision: policy.policy.revision + 1}};
        return send(policy);
      }
      return send(policyReadStatus === 200 ? policy : {detail: "Storage unavailable"}, policyReadStatus);
    }
    if (path === "/api/v1/storage/locations") {
      if (method === "POST") {
        if (locationStatus !== 200) return send({detail: "Location cannot be registered"}, locationStatus);
        const created = {...location(ARCHIVE), ...body, location_id: ARCHIVE, is_default: body.make_default};
        localLocations.push(created);
        return send(created);
      }
      return send({items: localLocations});
    }
    if (path.startsWith("/api/v1/storage/locations/") && method === "PATCH") {
      localLocations = localLocations.map(l => l.location_id === path.split("/").pop() ? {...l, ...body} : l);
      return send(localLocations[0]);
    }
    if (path === "/api/v1/storage/destinations") return send({items: [
      {node_id: LOCAL, node_key: "local", node_name: "Workshop", online: true, supported: true, locations: localLocations},
      {node_id: REMOTE, node_key: "archive", node_name: "Archive", online: true, supported: true, locations: [location(ARCHIVE, REMOTE)]},
      {node_id: "", node_key: "legacy", node_name: "Older Pi", online: true, supported: false, locations: []},
    ]});
    if (path === "/api/v1/storage/admission") return send(admissionsAllowed ? {allowed: true, code: "available", detail: "Destination is available", destination: {node_id: REMOTE, location_id: ARCHIVE}, policy_revision: policy.policy.revision, spooled: false} : {allowed: false, code: "quota_exceeded", detail: "The destination quota cannot accept this content."});
    if (path === "/api/v1/fleet/artifacts") return send(catalogStatus === 200 ? {items: url.searchParams.has("cursor") ? [artifact({artifact_id: PREVIEW, metadata: {name: "Second page"}, owner_online: true})] : catalog, next_cursor: url.searchParams.has("cursor") ? null : "50"} : {detail: "Catalog unavailable"}, catalogStatus);
    if (path.startsWith("/api/v1/artifacts/") && path.endsWith("/retention") && method === "PATCH") {
      if (retentionStatus !== 200) return send({detail: "admin role required"}, retentionStatus);
      catalog = catalog.map(a => a.artifact_id === path.split("/")[4] ? {...a, retention: body} : a);
      return send(catalog.find(a => a.artifact_id === path.split("/")[4]));
    }
    if (path === "/api/v1/transfers") return send({items: transfers, next_cursor: null});
    if (path.startsWith("/api/v1/transfers/") && path.endsWith("/retry")) {
      transfers = transfers.map(t => ({...t, state: "committed", offset: t.size_bytes, error_code: null, actual_owner_node_id: REMOTE}));
      return send(1);
    }
    if (path === "/api/v1/storage/migrations/preview") {
      const input = body as MigrationInput;
      previewResult = {preview_id: PREVIEW, expires_at: Date.now() / 1000 + (expiredPreview ? -10 : 300), source_location_id: input.source_location_id, destination: {...input.destination, location_id: input.destination.location_id || ARCHIVE}, remove_source: input.remove_source, item_count: 1, total_bytes: 100, can_start: !previewBlocked, blockers: previewBlocked ? ["An active capture still uses this location."] : [], items: [{artifact_id: ARTIFACT, name: "Workshop clip", kind: "recording", size_bytes: 100, action: input.remove_source ? "move" : "copy"}]};
      return send(previewResult);
    }
    if (path === "/api/v1/storage/migrations") {
      if (method === "POST") {
        const job: StorageMigration = {migration_id: PREVIEW, state: "running", phase: "copying", completed_items: 0, total_items: 1, bytes_done: 0, total_bytes: 100, detail: "Copying content before verification.", remove_source: previewResult!.remove_source, can_cancel: true, can_resume: false};
        jobs.push(job); return send(job);
      }
      return send({items: jobs});
    }
    if (path.endsWith("/cancel")) {jobs = jobs.map(j => ({...j, state: "cancelled", can_cancel: false, can_resume: true})); return send(jobs[0]);}
    if (path.endsWith("/resume")) {jobs = jobs.map(j => ({...j, state: "running", can_cancel: true, can_resume: false})); return send(jobs[0]);}
    const fixtures: Record<string, unknown> = {
      "/api/cameras": [], "/api/media": [], "/api/events": [],
      "/api/system": {version: "1.10.0", host: "workshop", node_id: LOCAL, node_name: "Workshop", node_roles: ["storage"], tailscale_installed: false, tailscale_running: false, access_url: "http://localhost:4173", local_url: "http://localhost:4173", media_bytes: 0, hidden_count: 0, host_model: "Isolated browser fixture", ram_gb: 8, cpu_count: 4, low_power: false},
      "/api/hosts": [{host: "workshop", node_key: "local", kind: "local", online: true, version: "1.10.0", camera_count: 0, proxy_prefix: "", node_id: LOCAL, node_name: "Workshop", node_roles: ["storage"]}, {host: "archive", node_key: "archive", kind: "peer", online: true, version: "1.10.0", camera_count: 0, proxy_prefix: "/proxy/archive", node_id: REMOTE, node_name: "Archive", node_roles: ["storage"]}],
      "/api/update": {current: "1.10.0", latest: "1.10.0", available: false},
      "/api/v1/node/config": {node_id: LOCAL, name: "Workshop", configured_roles: ["storage"], active_roles: ["storage"], restart_required: false},
      "/api/v1/node/capabilities": {api_version: "1", capabilities: [], actions: [], principal: {actor: "fixture", display_name: "Fixture", source: "local", verified: true, roles: principalRoles}},
      "/api/storage": {media_dir: "/fixture/media", custom_dir: "", is_default: true, writable: true, storage_enabled: true, disk_total: 50 * GIB, disk_free: 20 * GIB, disk_used: 30 * GIB, media_bytes: 0, media_count: 0, timelapse_bytes: 0, node: "", node_online: true, node_error: "", nodes: [], low_power_host: false, auto_record: false, record_tail_seconds: 5, retention_enabled: false, max_gb: 10, max_age_days: 30},
    };
    if (path in fixtures) return send(fixtures[path]);
    return send({detail: "Unavailable in isolated storage fixture"}, 404);
  });
  await new Promise<void>((resolve, reject) => {backend.once("error", reject); backend.listen(4174, "127.0.0.1", resolve);});
});
test.afterAll(async () => {await new Promise<void>((resolve, reject) => {backend.close(error => error ? reject(error) : resolve()); backend.closeAllConnections();});});
test.beforeEach(async ({page}) => {
  policy = {enabled: false, source_node_id: LOCAL, policy: {revision: 1, default_destination: {node_id: LOCAL, location_id: ROOT}, overrides: [], outage_policy: "destination_required", secondary_destination: null, zero_local_media: false, spool_max_bytes: 0, spool_max_age_seconds: 86400, workspace_max_bytes: GIB, artifact_max_bytes: 16 * GIB, source_cleanup: "after_primary_commit", retention: {enabled: false, max_age_seconds: 0, min_replicas: 1, protect: false}}};
  localLocations = [location()]; policyReadStatus = policyWriteStatus = catalogStatus = locationStatus = 200;
  retentionStatus = 200; principalRoles = ["admin"];
  catalog = [artifact()]; transfers = []; jobs = []; previewResult = undefined; previewBlocked = expiredPreview = false; admissionsAllowed = true; requests.length = 0;
  await page.addInitScript(() => sessionStorage.setItem("tailcam.booted", "1"));
});

test("mobile policy applies scoped overrides only on explicit save", async ({page}, info) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto("/storage");
  await expect(page.getByText("Legacy placement is still active.", {exact: false})).toBeVisible();
  await page.getByLabel("Default device", {exact: true}).selectOption(REMOTE);
  await page.getByLabel("Default location", {exact: true}).selectOption(ARCHIVE);
  await expect(page.getByLabel("Default device", {exact: true}).locator("option").filter({hasText: "Older Pi"})).toHaveAttribute("disabled", "");
  await page.getByRole("button", {name: "Add override", exact: true}).click();
  await page.getByLabel("Override 1 origin", {exact: true}).selectOption(LOCAL);
  await page.getByLabel("Override 1 camera ID (blank = any)", {exact: true}).fill("/dev/video0");
  await page.getByLabel("Override 1 content", {exact: true}).selectOption("training_sample");
  expect(requests.filter(r => r.method === "PATCH")).toHaveLength(0);
  await page.getByRole("button", {name: "Apply storage policy", exact: true}).click();
  await expect(page.getByText("Policy applied. New content uses revision 2.", {exact: false})).toBeVisible();
  const sent = requests.find(r => r.method === "PATCH")!.body;
  expect(sent.expected_revision).toBe(1);
  expect(sent.policy.default_destination).toEqual({node_id: REMOTE, location_id: ARCHIVE});
  expect(sent.policy.overrides[0]).toMatchObject({origin_node_id: LOCAL, camera_id: "/dev/video0", content_kind: "training_sample"});
  expect(requests.some(r => r.url.includes("/migrations") || r.method === "DELETE")).toBe(false);
  expect(await page.locator(".storage-screen").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({path: info.outputPath("storage-policy-mobile.png")});
  await page.getByRole("heading", {name: "Storage", exact: true}).scrollIntoViewIfNeeded();
  await page.screenshot({path: info.outputPath("storage-policy-mobile-top.png")});
});

test("policy conflicts preserve the draft and explicit reload fetches current revision", async ({page}) => {
  await page.goto("/storage");
  await page.getByLabel("Default device", {exact: true}).selectOption(REMOTE);
  policy.policy.revision = 2;
  await page.getByRole("button", {name: "Apply storage policy", exact: true}).click();
  await expect(page.getByRole("alert").filter({hasText: "Your draft is preserved"})).toBeVisible();
  await expect(page.getByLabel("Default device", {exact: true})).toHaveValue(REMOTE);
  await page.getByRole("button", {name: "Reload saved policy", exact: true}).click();
  await expect(page.getByLabel("Default device", {exact: true})).toHaveValue(LOCAL);
  await page.getByLabel("Default device", {exact: true}).selectOption(REMOTE);
  await page.getByRole("button", {name: "Apply storage policy", exact: true}).click();
  await expect(page.getByText("Policy applied. New content uses revision 3.", {exact: false})).toBeVisible();
  expect(requests.filter(r => r.method === "PATCH").at(-1)!.body.expected_revision).toBe(2);
});

test("zero local media cannot select a local outage queue", async ({page}) => {
  await page.goto("/storage");
  await page.getByLabel("If the destination is unavailable").selectOption("local_spool");
  await page.getByLabel("Local queue limit (GiB)").fill("2");
  await page.getByLabel("Keep no local media on this source").check();
  await expect(page.getByLabel("If the destination is unavailable")).toHaveValue("destination_required");
  await expect(page.getByLabel("If the destination is unavailable").locator('option[value="local_spool"]')).toHaveAttribute("disabled", "");
  await page.getByLabel("Temporary workspace limit (GiB)").fill("0");
  await page.getByRole("button", {name: "Apply storage policy", exact: true}).click();
  await expect(page.getByText("Policy applied.", {exact: false})).toBeVisible();
  const sent = requests.find(r => r.method === "PATCH")!.body.policy;
  expect(sent).toMatchObject({zero_local_media: true, outage_policy: "destination_required", workspace_max_bytes: 0});
});

test("legacy controls remain when policy is unavailable and disappear after enable", async ({page}) => {
  policyReadStatus = 404;
  await page.goto("/settings");
  await expect(page.getByRole("button", {name: "Set location", exact: true})).toBeVisible();
  await expect(page.getByText("Its existing storage settings remain available.", {exact: false})).toBeVisible();
  policyReadStatus = 200; policy.enabled = true;
  await page.reload();
  await expect(page.getByText("Unified policy active · revision 1")).toBeVisible();
  await expect(page.getByRole("button", {name: "Set location", exact: true})).toHaveCount(0);
  await expect(page.getByRole("switch", {name: "Automatically delete old media to stay under the limits"})).toHaveCount(0);
  await expect(page.getByRole("switch", {name: "Save a clip when motion is detected"})).toBeVisible();
});

test("admission denial is explicit, non-reserving, and clears after input changes", async ({page}) => {
  admissionsAllowed = false;
  localLocations[0].free_bytes = null; localLocations[0].allocatable_bytes = null;
  await page.goto("/storage");
  await expect(page.getByRole("region", {name: "Storage locations"})).toContainText("Not reported");
  await page.getByRole("button", {name: "Check admission", exact: true}).click();
  await expect(page.getByText("Cannot accept this content", {exact: true})).toBeVisible();
  await expect(page.getByText("The destination quota cannot accept this content.")).toBeVisible();
  expect(requests.filter(r => r.method !== "GET").map(r => r.url)).toEqual(["/api/v1/storage/admission"]);
  expect(requests.find(r => r.url.endsWith("/admission"))!.body).toMatchObject({kind: "recording", camera_id: "", origin_node_id: LOCAL, requires_workspace: false});
  await page.getByLabel("Estimated content size (MiB)").fill("2");
  await expect(page.getByText("Cannot accept this content", {exact: true})).toHaveCount(0);
});

test("location limits update by immutable ID without editing path or deleting content", async ({page}) => {
  await page.goto("/storage");
  await page.getByRole("button", {name: "Edit limits for Camera disk", exact: true}).click();
  await page.getByLabel("Quota (GiB, 0 = no quota)", {exact: true}).fill("30");
  await page.getByLabel("Keep free (GiB)", {exact: true}).fill("4");
  await page.getByRole("button", {name: "Save location limits", exact: true}).click();
  await expect(page.getByText("Location limits saved.")).toBeVisible();
  const sent = requests.find(r => r.method === "PATCH")!;
  expect(sent.url).toBe(`/api/v1/storage/locations/${ROOT}`);
  expect(sent.body).toEqual({quota_bytes: 30 * GIB, reserve_bytes: 4 * GIB});
  expect(requests.some(r => r.method === "DELETE")).toBe(false);
});

test("catalog retains offline ownership and encodes filters while downloads stay same origin", async ({page}) => {
  await page.goto("/storage?tab=content");
  await expect(page.getByRole("heading", {name: "Workshop clip"})).toBeVisible();
  await expect(page.getByText("The owner is offline.", {exact: false})).toBeVisible();
  await expect(page.getByRole("link", {name: "Open content", exact: true})).toHaveCount(0);
  await page.getByLabel("Catalog camera ID").fill("/dev/video0 & state=deleted");
  await page.getByRole("button", {name: "Filter camera"}).click();
  await expect.poll(() => requests.some(r => r.url.includes("camera_id=%2Fdev%2Fvideo0+%26+state%3Ddeleted"))).toBe(true);
  await page.getByRole("button", {name: "Load more content"}).click();
  await expect(page.getByRole("heading", {name: "Second page"})).toBeVisible();
  await expect(page.getByRole("link", {name: "Open content", exact: true})).toHaveAttribute("href", `/api/v1/artifacts/${PREVIEW}/content`);
  expect(requests.some(r => r.url.includes("untrusted.invalid"))).toBe(false);
});

test("transfer receiving and verifying do not claim committed content", async ({page}) => {
  transfers = [{transfer_id: PREVIEW, artifact_id: ARTIFACT, location_id: ROOT, offset: 100, size_bytes: 100, state: "verifying", created_at: 1700000000, updated_at: 1700000000, error_code: null}];
  await page.goto("/storage?tab=transfers");
  await expect(page.getByRole("heading", {name: "Verifying content", exact: true})).toBeVisible();
  await expect(page.getByRole("heading", {name: "Verified and committed", exact: true})).toHaveCount(0);
  await expect(page.getByRole("progressbar")).toHaveAttribute("value", "100");
  expect(requests.filter(r => r.method !== "GET")).toHaveLength(0);
});

test("failed outbound transfer retries the durable job and refreshes commitment", async ({page}) => {
  transfers = [{transfer_id: PREVIEW, artifact_id: ARTIFACT, location_id: ROOT, offset: 0, size_bytes: 100, state: "failed", created_at: 1700000000, updated_at: 1700000000, error_code: "owner_unavailable", direction: "outbound", requested_destination: {node_id: REMOTE, location_id: ARCHIVE}, actual_owner_node_id: LOCAL}];
  await page.goto("/storage?tab=transfers");
  await expect(page.getByText("Outbound from this device", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "Retry transfer", exact: true}).click();
  await expect(page.getByRole("heading", {name: "Verified and committed", exact: true})).toBeVisible();
  await expect(page.getByText("Transfer verified at destination.", {exact: true})).toBeVisible();
  expect(requests.filter(r => r.method === "POST").map(r => r.url)).toEqual([`/api/v1/transfers/${PREVIEW}/retry`]);
});

test("artifact retention edits are scoped to the local owner and preserve rejected drafts", async ({page}) => {
  catalog = [artifact({owner_node_id: LOCAL, owner_online: true, location_id: ROOT}), artifact({artifact_id: PREVIEW, metadata: {name: "Remote clip"}})];
  await page.goto("/storage?tab=content");
  await expect(page.getByRole("button", {name: "Edit artifact retention", exact: true})).toHaveCount(1);
  await expect(page.getByText("Edit this artifact's retention on its current owner device.", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "Edit artifact retention", exact: true}).click();
  await page.getByLabel("Protect this artifact from deletion").check();
  await page.getByLabel("Enable expiry for this artifact").check();
  await page.getByLabel("Artifact expiry age (days)").fill("14");
  await page.getByLabel("Artifact minimum verified replicas").fill("2");
  retentionStatus = 403;
  await page.getByRole("button", {name: "Save artifact retention", exact: true}).click();
  await expect(page.getByRole("alert").filter({hasText: "administrator connection"})).toBeVisible();
  await expect(page.getByLabel("Protect this artifact from deletion")).toBeChecked();
  await expect(page.getByLabel("Artifact expiry age (days)")).toHaveValue("14");
  retentionStatus = 200;
  await page.getByRole("button", {name: "Save artifact retention", exact: true}).click();
  await expect(page.getByText("Artifact retention saved.", {exact: true})).toBeVisible();
  const sent = requests.filter(r => r.method === "PATCH").at(-1)!;
  expect(sent.url).toBe(`/api/v1/artifacts/${ARTIFACT}/retention`);
  expect(sent.body).toEqual({enabled: true, max_age_seconds: 14 * 86400, min_replicas: 2, protect: true});
  principalRoles = ["viewer"];
  await page.reload();
  await expect(page.getByText("An administrator connection is required to edit artifact retention.", {exact: true})).toBeVisible();
  await expect(page.getByRole("button", {name: "Edit artifact retention", exact: true})).toHaveCount(0);
});

async function fillMigration(page: import("@playwright/test").Page) {
  await page.goto("/storage?tab=migrations");
  await page.getByLabel("Source location on this device").selectOption(ROOT);
  await page.getByLabel("Migration destination device", {exact: true}).selectOption(REMOTE);
  await page.getByLabel("Migration destination location", {exact: true}).selectOption(ARCHIVE);
}

test("migration defaults to copy and changed inputs invalidate the reviewed plan", async ({page}) => {
  await fillMigration(page);
  await expect(page.getByLabel("Operation", {exact: true})).toHaveValue("copy");
  await page.getByRole("button", {name: "Preview existing content", exact: true}).click();
  await expect(page.getByRole("region", {name: "Migration preview"})).toContainText("Workshop clip");
  expect(requests.filter(r => r.method === "POST").map(r => r.url)).toEqual(["/api/v1/storage/migrations/preview"]);
  expect(requests.find(r => r.method === "POST")!.body.remove_source).toBe(false);
  await page.getByLabel("Operation", {exact: true}).selectOption("move");
  await expect(page.getByRole("region", {name: "Migration preview"})).toHaveCount(0);
  await expect(page.getByRole("button", {name: "Start reviewed copy", exact: true})).toHaveCount(0);
});

test("reviewed move requires explicit cleanup acknowledgment and sends only preview token", async ({page}, info) => {
  await fillMigration(page);
  await page.getByLabel("Operation", {exact: true}).selectOption("move");
  await page.getByRole("button", {name: "Preview existing content", exact: true}).click();
  await expect(page.getByRole("button", {name: "Start reviewed move", exact: true})).toBeDisabled();
  await page.getByLabel("I reviewed these items and approve removing their source copies after verification.").check();
  const reviewed = page.getByRole("region", {name: "Migration preview"});
  await expect(reviewed).toContainText("Camera disk");
  await expect(reviewed).toContainText("Workshop");
  await expect(reviewed).toContainText("Archive disk");
  await page.screenshot({path: info.outputPath("storage-migration-review-desktop.png")});
  await page.getByRole("button", {name: "Start reviewed move", exact: true}).click();
  await expect(page.getByText("Move started. Progress is shown below.")).toBeVisible();
  expect(requests.find(r => r.method === "POST" && r.url === "/api/v1/storage/migrations")!.body).toEqual({preview_id: PREVIEW});
  await page.getByRole("button", {name: "Cancel migration", exact: true}).click();
  await expect(page.getByRole("button", {name: "Resume migration", exact: true})).toBeVisible();
});

test("expired or blocked migration previews cannot start", async ({page}) => {
  expiredPreview = true; previewBlocked = true;
  await fillMigration(page);
  await page.getByRole("button", {name: "Preview existing content", exact: true}).click();
  await expect(page.getByText("An active capture still uses this location.")).toBeVisible();
  await expect(page.getByText("This preview expired.", {exact: false})).toBeVisible();
  await expect(page.getByRole("button", {name: "Start reviewed copy", exact: true})).toBeDisabled();
  expect(requests.some(r => r.method === "POST" && r.url === "/api/v1/storage/migrations")).toBe(false);
});
