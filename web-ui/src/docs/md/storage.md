# Storage destinations

Open **Storage** to choose where TailCam saves new content. Each originating node
has its own policy. A destination identifies a node by its persistent UUID and a
registered folder by its location UUID, so renaming a computer does not change
which computer owns its content.

## Set up a destination

1. Open TailCam on the computer that will hold the files. Enable its storage role
   if necessary, then restart that node.
2. In **Storage → Locations**, register an existing absolute folder. For an
   external drive, mount it first. Set a quota and reserved free space if needed.
3. On the camera or processing node, open **Storage → Policy** and select that
   node and location. Check admission for the intended content type, then apply.

Until you apply this policy, existing recording destinations and folders retain
their earlier behavior. Applying affects new operations. Active recordings,
timelapses and training runs keep the destination and budget admitted when they
started. A conflicting save asks you to reload the latest revision.

The default covers recordings, snapshots, thumbnails, timelapse frames, videos,
smoothed videos, analysis evidence, training samples, annotation revisions,
managed model outputs and exports. Overrides choose a different destination for
a content type, camera, or camera plus content type. The most specific matching
override wins. A camera selector includes its originating node UUID.

## When a destination is unavailable

**Destination required** rejects new work that cannot reach its owner.
**Bounded local spool** permits a finite number of bytes locally, then retries
delivery with backoff. An expired spool stops retrying and stays visible for
inspection; TailCam keeps its reserved bytes until it is resolved. It does not
silently delete the only copy to make room. **Secondary destination** uses the
selected alternative and records both requested and actual owners.

**Zero local media** requires a remote owner and forbids local spooling and
temporary media workspaces. Operations that need a local encoder or model
workspace are rejected before launch. It does not provide offline capture.
Identity, configuration and the SQLite journal remain on the originating node.

Each upload uses bounded chunks, acknowledged offsets and SHA-256 verification.
A file becomes committed only after the destination confirms all bytes. Transfer
retries retain the same artifact identity. The Content and Transfers tabs keep
pending, committed, replicated and failed states distinct. Cached catalog entries
remain visible when their owner is offline; viewing their bytes may be unavailable.

## Drives and temporary work

TailCam records a folder marker and filesystem identity. A missing or replaced
mount blocks writes; restarting does not recreate an absent external media root
on the boot disk. Reconnect the original drive or register a new location and
explicitly change the destination.

Quotas account for committed content and reserved transfers/workspaces. Native
byte writes are checked before writing. FFmpeg and model libraries use finite,
monitored workspaces; this is not an operating-system disk quota. Failed work can
retain its workspace for recovery and continues to consume its reservation.

Managed YOLO training requires pre-provisioned weights: an existing `.pt` file or
a managed `model:<id>` reference. Training that would download weights into an
uncontrolled library cache is rejected. Florence/Qwen fine-tuning under unified
storage requires later process-level cache containment; older unmanaged training
behavior remains available before unified storage is enabled. Merely installing
a training package does not verify GPU or model compatibility.

## Move existing content

Changing the default does not move old files. In **Migrations**, choose a source
and destination, then preview the content and required space. Review skipped
files and choose copy or move. A preview expires after ten minutes; changed source
bytes require a fresh preview.

A move copies and verifies content, updates the artifact location, then removes
the source. Existing gallery, sample and timelapse URLs resolve through stable
aliases. Active captures are excluded. Cancel stops between files; an in-flight
file may finish. After interruption, inspect the paused job and resume it.

Retention is opt-in and recorded per artifact. Protected content and active
dependencies are preserved. Raw frames and related outputs are cataloged too.
Replica cleanup preserves the configured minimum; explicit content expiration is
a separate decision to remove the expired content. Failed file deletion preserves
its catalog entry instead of reporting success.

## Access and API

The storage protocol uses the existing verified TailCam principal. Viewers inspect
catalogs, operators transfer content, and administrators change policy, locations
and migrations. Peer discovery alone does not grant permission: unattended node
transfers need a matching Tailscale application-capability grant. Older nodes
without this protocol appear unavailable as storage destinations.

The versioned API is rooted at `/api/v1`: `/storage/policy`, `/storage/locations`,
`/storage/destinations`, `/storage/admission`, `/artifacts`, `/fleet/artifacts`,
`/artifacts/changes`, `/transfers` and `/storage/migrations`. File content is served
by `/artifacts/{artifact_id}/content`. Manifests are limited to 1 MiB and upload
chunks to 4 MiB. An upload chunk supplies its offset and `X-Chunk-SHA256`; commit
checks the full artifact checksum. Arbitrary fetch URLs and destination file paths
are not accepted in transfer manifests.

### Policy journal and peer identity recovery

After you apply a policy, its revision and enablement are authoritative in the
node's local SQLite journal. Editing TOML alone does not replace an applied policy;
TOML values only seed an unapplied installation. Back up the journal together with
configuration and node identity. Keep this database local to the node.

Approved peers are bound to their persistent UUID and first observed address,
including across restarts. A conflicting claim cannot silently redirect content
when the original endpoint goes offline. After deliberately replacing a configured
address, remove duplicate identities, then an administrator can clear its binding
with `POST /api/v1/storage/peer-identities/{node_id}/reset`. The next discovery binds
the replacement address. This reset does not rewrite existing artifacts or owners.
