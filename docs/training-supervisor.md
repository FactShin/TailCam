# Running a bounded training supervisor

TailCam 1.11 separates the external agent's decisions from worker execution.
TailCam owns the approved policy, immutable inputs, finite experiment budget,
durable jobs, cancellation and saved evidence. MCP connects an external host;
it does not keep that host awake. No training run is started by this guide.

## Approve the objective first

Use **Workloads → Training Supervisor** to review a dataset, select its permitted
cameras and classes, choose registered base models and worker UUIDs, and set
parameter ranges, allowed seeds, maximum experiments and resource limits.
Record an evaluation reference and explicit metric criteria. Approval creates a
persistent record; it does not launch an experiment.

The dataset revision is a SHA-256 of the current content and labels, including
annotations. The old numeric dataset version is insufficient because manual
edits did not consistently increment it. Preparation checks the hash before
and after freezing the permitted subset. If the data changed, review and
approve a new record. Existing approvals are immutable.

Model activation is absent from the policy's permitted actions and its schema
only accepts `activation_enabled: false`. The already-active model remains
unchanged. Model selection is an existing registry ID, not an agent-provided
filesystem path or download URL.

The policy admits one experiment at a time within this supervision. The job
scheduler also enforces node-wide capacity across other callers. TailCam
reserves the entire per-experiment wall budget before preparing inputs; it does
not refund unused reservations into extra experiments. Preparation and queue
time count toward the absolute job deadline. Retry attempts stay inside that
deadline and their configured attempt limit.

## Connect a restricted host

Configure the agent's Tailscale identity with TailCam `viewer` and `operator`
roles under the existing `factshin.github.io/cap/tailcam` app capability.
Exclude `admin`. Personal-mode identities without an explicit restricted grant
may have administrator access; hiding tools in a host's menu is insufficient.
Use the existing authenticated `/mcp` endpoint and supported MCP protocol
negotiation. Do not paste credentials into prompts or record them in logs.

An administrator approves policy through the dashboard or
`approve_training_supervision`. The restricted agent uses this initial tool
allowlist:

| Tool | Input and purpose |
|---|---|
| `get_training_dataset_revision` | `{dataset_id}`: current content revision and scope candidates |
| `list_training_supervisions` | `{cursor?, limit?}`: saved approvals and remaining budgets |
| `get_training_supervision` | `{supervision_id}`: current state before each new decision |
| `submit_training_experiment` | `{supervision_id, experiment}`: finite approved experiment |
| `heartbeat_training_supervisor` | `{supervision_id, heartbeat}`: external host availability |
| `stop_training_supervision` | `{supervision_id}`: prevent new work and request worker stop |
| `finish_training_supervision` | `{supervision_id, reason}`: finish when no job is active |
| `get_training_supervision_report` | `{supervision_id}`: all runs, metrics, artifacts and limitations |
| `list_training_supervision_events` | `{supervision_id, after?}`: replayable polling cursor |

Keep the existing dataset/sample/model inspection tools available as needed.
Do not include `activate_model`, `deactivate_model`, unrestricted
`start_training_run`, model registration/deletion, or arbitrary administrative
operations in this agent identity. The REST endpoints enforce the same
restrictions. In-process MCP calls retain the caller's identity; they are not
promoted to local administrator.

Administrative REST settings, plugin installation and secret-bearing
integration/notification configuration also require admin. Their generic-proxy
paths are denied because that hop cannot preserve the caller's principal.
An administrator opens the destination node directly for those operations.

The `experiment` object contains `idempotency_key`, `base_model_id`,
`worker_node_id`, `epochs`, `image_size`, `seed` and a short `reason`. TailCam
validates each field against the approval before reserving work. Persist the
key **before** sending. After a timeout, retry the identical request and key;
do not invent a new key to work around an uncertain response. Reusing a key
with changed settings returns 409.

The `heartbeat` object contains `session_id`, a decision (`inspect`, `wait`,
`experiment`, `stop` or `finish`), a reason and optional `next_check_at`.
This heartbeat is separate from the worker heartbeat and epoch/metric progress.
After the configured timeout the UI reports the supervisor disconnected, while
already-accepted work remains governed by TailCam's deadline.

## Persistent-host loop

Persist the supervision ID, current experiment key and request, job ID, host
session ID, event cursor and next planned check in the host's durable state.
Use a scheduler or persistent MCP host that actually supports later wakeups.
The following is a workflow contract, not a claim that a particular Hermes or
Codex installation has been configured or tested:

1. Reconnect and read `get_training_supervision`. Reconcile saved IDs before
   proposing work. An existing queued/running/committing experiment stays the
   current experiment.
2. Record a heartbeat and poll the current job or supervision. Persist returned
   event IDs/cursor. Polling is authenticated; replayed IDs suppress duplicates.
3. If an experiment succeeded and another is allowed, choose parameters within
   the immutable ranges and reserve a new stable idempotency key. Submit once,
   then reconcile that key until its outcome is known.
4. For known transient errors, allow only the worker's approved retry policy.
   Unknown failures stop the series and preserve diagnostic references. Do not
   call the generic job retry route to bypass supervision budgets.
5. To stop, request stop and keep polling until the record is stopped or another
   explicit terminal state is reported. `stop_requested` means termination is
   still being reconciled.
6. Finish or exhaust the approved series, then read the report. Explain missing
   measurements and recommend a candidate for human review. Do not activate it.

A lost host does not create new experiments. A lost preparation retains its
reservation and deterministic job identity; recovery observes the existing
journal and expires abandoned preparation at its reserved deadline. A lost
worker is handled separately by the job leases and process deadline.

## Reading the result honestly

The report includes every experiment and job ID, original settings, dataset
hash, worker/placement, timestamps, reported metrics, output references and
known limitations. Ranking uses the first declared comparison metric when it
is available. A high training metric is not a held-out evaluation, proof of
printer safety, or deployment approval. Evaluation coverage, promotion,
canaries and rollback are later roadmap work.

The mock MCP-host test covers a three-experiment series, reconnect replay,
simultaneous admission, policy rejection and unchanged activation. It does not
prove a real ML engine's accuracy, GPU support, or a particular external host's
ability to run unattended. Verify those separately in the intended environment.
