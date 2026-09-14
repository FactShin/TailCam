# Storage protocol and recovery

TailCam 1.10 adds `/api/v1/storage`, `/api/v1/artifacts` and `/api/v1/transfers`.
The generic peer proxy rejects the entire `/api/v1` namespace because it cannot
preserve the caller's role across a peer hop. Storage operations use direct,
role-checked routes. Viewing requires a verified viewer; uploads and handoffs
require operator; policy, location, retention, migration and identity-reset
operations require administrator. Receiving canonical bytes also requires the
destination's active storage role. Discovery is not an authorization grant.

## Policy authority and identity

Once applied through the Storage API, policy and enablement are authoritative in
the node's local SQLite journal. The in-memory configuration mirrors the effective
value; editing TOML alone does not overwrite an applied policy. Back up that
journal with configuration and identity. TOML policy values seed an unapplied
installation. No database is shared over a media mount or copied as a content
artifact.

An operation snapshots its policy, destination, temporary workspace and retention
limits before execution. Changes affect later work. Producer roles authorize
analysis/training runtime caches; local canonical content and spools additionally
require storage. `zero_local_media` disallows all media workspaces and spooling.

Peer UUIDs are obtained from approved peer endpoints. The first observed address
binding persists across process restart. Conflicting UUID claims cannot silently
redirect content when the original endpoint goes offline. After deliberately
replacing a configured address, an administrator can clear that node's binding
using `POST /api/v1/storage/peer-identities/{node_id}/reset`; its next discovery
establishes the replacement. Remove duplicate configured identities first.
Resetting this binding does not rewrite existing artifacts or their owners.

## Upload and content invariants

Create a transfer with `POST /api/v1/transfers` containing `artifact`,
`destination` and `idempotency_key`. The artifact includes UUID identity, origin,
owner, kind, length, SHA-256, timestamps, requested destination and policy revision.
Manifests cannot supply an arbitrary destination filename or source URL.

`PUT /api/v1/transfers/{id}/chunks?offset=N` accepts at most 4 MiB and requires
`X-Chunk-SHA256`. Repeating acknowledged bytes is idempotent; inconsistent offsets
or hashes fail without advancing. `GET /api/v1/transfers/{id}` reports the durable
offset. `POST /api/v1/transfers/{id}/commit` verifies total bytes and SHA-256, then
renames and records the committed artifact. Cancellation releases safely removable
receiver reservations. Repeated begin/commit operations retain artifact identity.

Catalog pages are bounded. Imported entries cannot change immutable content
identity or claim another owner's existing artifact. The current owner can
publish a verified ownership handoff. Offline/recovered observations update the
local cache without republishing a change-feed loop. Private source paths and
transfer manifests are excluded from fleet catalog and public job records.

Peer JSON and file reads request identity encoding and reject compressed responses.
Content proxies validate declared lengths and byte ranges against catalog size,
bound streamed bytes, disallow redirects and add a one-hop guard. Safe image/video
types may display inline; other artifacts download with `nosniff`. Byte-range reads
support a single requested range. Unavailable owners preserve their indexed entries.

## Migration and deletion

Preview freezes source identities, file checksums, destination location and whether
source removal was requested. It expires after ten minutes. Start validates that
reviewed bytes and mounts have not changed. Jobs persist private manifests in the
local journal, pause after restart and resume explicitly. An acknowledged target
commit reconciles a crash between source cleanup and progress recording.

Moving removes only the reviewed source location's copy, after verification and
reference updates. Unreviewed replicas remain. Copy cleanup honors minimum replica
count; explicit canonical retention expiry is a distinct opt-in deletion policy.
Protected artifacts and active dependencies are not expired automatically. Failed
unlink leaves its catalog state intact. Stopped/failed producer workspaces retain
reservations until verified cleanup completes, including across restart.

## Limits

Byte ingestion and native producer writes have prewrite budget checks. Opaque
encoders and ML libraries run in reserved, monitored workspaces, which can exceed
a threshold before an external call returns; these are not kernel filesystem
quotas. Model/cache containment failures reject unified training admission.
Cross-node workload execution and hard worker process budgets belong to 1.11.
Real media-drive removal and camera/GPU throughput require separate hardware tests.
