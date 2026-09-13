# Workload placement and durable execution

Version: 1.11.0. These contracts build on the node identities and artifact
protocol introduced in 1.9 and 1.10. They do not imply that an optional ML engine
or a GPU has been installed or exercised.

## Choosing where work runs

Open **Workloads → Placement** to select each task independently:
`live_detection`, `motion_description`, `printer_analysis`, `timelapse_encode`,
`timelapse_interpolate`, `labeling`, and `training`. Speech recognition,
conversation and speech synthesis remain reserved for 1.15.

A manual target remains authoritative. Auto considers only explicitly approved
node UUIDs with observed task readiness and sufficient configured admission
capacity. A fallback requires a separately approved target. Once a worker may
have accepted a durable assignment, a lost reply never authorizes execution on
a different node. TailCam reconciles the same remote job identifier.

The placement revision, requested target, actual target, model/provider,
fallback reason and budgets are copied into the job or live session. Changing
placement affects new work. A standalone Ollama endpoint has a provider ID; it
does not acquire a TailCam node identity. Registering an endpoint does not prove
its model is available.

Registered external endpoints use the Ollama protocol in this release. Other
plugin analyzers require a worker adapter and are reported as unsupported;
TailCam does not silently send their images through an Ollama replacement.
An existing explicit `detection.node` keeps the bounded legacy remote detector
until a task-specific placement route is saved. That compatibility route can
work with an older peer; it cannot report a worker UUID the peer never supplied.

`GET /api/v1/workloads/policy` returns `{source_node_id, policy}`. Admin-only
`PATCH` accepts `{expected_revision, policy}` and returns 409 on a stale
revision. Worker and provider lists use `{items}`. `GET /workloads/worker`
returns only the responding worker's local observations, avoiding recursive
fleet discovery. Provider writes require admin. Worker addresses come from the
persistent identity bindings used by storage, never from an arbitrary job URL.

## Durable jobs

`POST /api/v1/jobs` accepts `{idempotency_key, spec}` and requires admin.
The spec carries a stable UUID, origin node, task or ordered stage graph,
immutable artifact inputs, preallocated output slots, placement, resource
budget, priority, absolute deadline and maximum attempts. Reusing a key with a
different request returns a conflict. Accepted input hashes and sizes are
checked again when the selected worker materializes them.

Accepted inputs and committed dependencies carry expiring retention holds at
their canonical storage owner. Holds bind the job/coordinator identity to the
artifact's digest and size, expire within the original deadline, and prevent
deletion or ownership migration while downstream work still needs those bytes.
Terminal jobs release them best effort; expiry also handles coordinator loss.

`GET /api/v1/jobs` accepts `task`, `state`, `cursor` and `limit`, and returns
`{items, next_cursor}`. Job detail exposes stage progress, requested versus
actual placement, timestamps, errors, outputs and permitted actions.
`POST /jobs/{id}/cancel` requests cancellation; the response is not a claim
that the child has already stopped. `POST /jobs/{id}/retry` is restricted by
the saved deadline, attempts and state. A supervised training job cannot use
that route to obtain a fresh experiment budget. Any stage containing
unsupervised training requires admin to retry.

`GET /jobs/{id}/events?after=...` supplies stable event IDs and a replay cursor.
Authenticated polling is the initial notification contract. A client persists
its cursor and deduplicates event IDs. No unauthenticated callback or implicit
agent scheduler is created.

SQLite transactions reserve queue and execution capacity. Leases include a
worker session, attempt identity and unpredictable fencing token. Heartbeats
are independent of epoch updates and remote HTTP responses. A long epoch alone
does not prove a stalled worker. A stage can be queued, leased, running,
committing, waiting for a retry or worker, or terminal; deadline and cancellation
are explicit terminal outcomes.

## Publication and recovery

An execution child writes only staged relative files and a bounded checksum
manifest. It has no job-journal handle or canonical-publication authority.
The parent validates paths, file types, sizes and hashes, then durably selects
one fenced attempt for publication. The output artifact UUIDs were allocated
before execution. A crash while committing reconciles those outputs instead
of blindly retraining or re-encoding after an ambiguous acknowledgement.

Committed upstream stage outputs survive a later stage's retry. Domain records
retain their existing numeric training-run and timelapse IDs, with job and node
references added as projections. This preserves existing UI/media links.
Checkpoint continuation is not implied: it requires explicit backend support;
otherwise an eligible failed stage restarts within its original budget.

Incoming delegated execution uses admin-only `/api/v1/jobs/execute`. The
coordinator must resolve to an approved bound peer, and every incoming stage
must target this worker. Workers never route received assignments onward.
Use an administrative TailCam service identity for delegation; a restricted
supervising agent must have viewer/operator grants, not that service identity.

Unified storage requires verified source UUIDs for remote capture. Compatibility
capture with unified storage disabled can still accept an older peer without
one; later artifact adoption identifies the storing node and cannot establish
that older camera node's original UUID. Do not treat that legacy provenance as
equivalent to an identity-verified capture.

## Keeping capture responsive

Long operations run in owned child processes with an absolute deadline and
cancellation escalation. POSIX uses an owned process group and parent-death
guardian; Windows uses a Job Object with kill-on-close and an initial handshake
before work starts. TailCam terminates only the process tree it created, never
the camera HTTP server or a PID recovered from an old journal.

Input staging currently uses bounded synchronous storage operations in the
parent. Deadline checks prevent late publication, but an operating-system disk
or DNS stall during staging cannot be forcibly interrupted by the compute-child
watchdog. The hard process-tree deadline covers computation; it is not a
portable guarantee for every filesystem operation on the host.

Live detection uses persistent local model actors and keeps one running frame
plus the newest pending frame. Superseded frames do not accumulate in the
durable queue. A session freezes its placement. An empty successful detection
result and an unavailable inference result are distinct.

Printer analysis uses its dedicated printer-health contract. Event, printer,
labeling and long-running task inputs can be immutable artifact references;
retries reuse those references rather than upload the same image repeatedly.

## Resource and platform limits

Configured admission capacity is not a hardware benchmark. CPU-thread settings,
exclusive accelerator slots, cumulative concurrency, absolute wall deadlines,
scratch reservations and bounded outputs limit accepted work. Runtime
availability and device choice are reported separately. Memory is an admission
reservation, not measured or capped process-tree RSS; native libraries can
allocate beyond it. Scratch is monitored but can temporarily exceed its
reservation between checks.

`cpu_seconds` is a conservative admission allowance, not measured CPU
consumption: it must cover `cpu_threads × wall_seconds`. Tighter CPU-time
budgets are rejected because this worker has no portable process-tree CPU
meter. CPU thread settings guide supported runtimes; they are not a kernel
CPU quota.

This release does **not** claim a kernel-enforced aggregate scratch quota or a
portable hard memory limit. A request requiring either unsupported hard limit
fails before execution. Managed caches and temporary files stay beneath the
worker scratch root. Zero-local-media policy forbids scratch on that node;
choose a worker that permits the required workspace.

The existing small built-in detector can provision only its fixed supported
model into the admitted child workspace, with bounded download bytes and the
child deadline. After successful inference, validated supported files are saved
in the existing managed model cache for later offline sessions; scratch is
still released. No caller-supplied model URL is accepted. Training still needs
registered model artifacts; it does not silently provision arbitrary weights.

Training requires prepared model artifacts and its optional engine on the
selected worker. Dataset preparation freezes a permitted subset on the dataset
owner/coordinator; it needs bounded scratch there. An arbitrary remote dataset
ID is not a local dataset. Real camera disconnect, CUDA/MPS/other accelerator,
optional model-engine and Windows process-tree claims require their own
platform validation; a synthetic or fake backend test is not that evidence.
