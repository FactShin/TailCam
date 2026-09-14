export const CONTENT_KINDS = ["recording", "snapshot", "thumbnail", "timelapse_frame", "timelapse_video", "timelapse_smooth", "analysis_evidence", "training_sample", "annotation", "model_output", "export"] as const;
export type ContentKind = typeof CONTENT_KINDS[number];
export interface DestinationRef { node_id: string; location_id: string | null }
export interface StorageRetention { enabled: boolean; max_age_seconds: number; min_replicas: number; protect: boolean }
export interface StorageOverride { origin_node_id: string | null; camera_id: string | null; content_kind: ContentKind | null; destination: DestinationRef }
export interface UnifiedStoragePolicy {
  revision: number; default_destination: DestinationRef; overrides: StorageOverride[];
  outage_policy: "destination_required" | "local_spool" | "secondary";
  secondary_destination: DestinationRef | null; zero_local_media: boolean;
  spool_max_bytes: number; spool_max_age_seconds: number; workspace_max_bytes: number;
  artifact_max_bytes: number; source_cleanup: "after_primary_commit" | "retain";
  retention: StorageRetention;
}
export interface StoragePolicyStatus { enabled: boolean; source_node_id: string; policy: UnifiedStoragePolicy }
export interface StorageLocation {
  location_id: string; node_id: string; label: string; path: string; marker: string;
  device: number; inode: number; created_at: number; quota_bytes: number; reserve_bytes: number;
  is_default: boolean; state: "ready" | "missing" | "changed" | "unwritable";
  used_bytes: number; reserved_bytes: number; free_bytes: number | null; allocatable_bytes: number | null;
}
export interface StorageDestination { node_id: string; node_key: string; node_name: string; online: boolean; supported: boolean; locations: StorageLocation[] }
export interface LocationInput { label: string; path: string; quota_bytes: number; reserve_bytes: number; make_default: boolean }
export interface AdmissionInput { origin_node_id: string; camera_id: string; kind: ContentKind; size_bytes: number; requires_workspace: boolean }
export interface StorageAdmission {
  allowed: boolean; code?: string; detail?: string; kind?: ContentKind; destination?: DestinationRef;
  requested_destination?: DestinationRef; policy_revision?: number; outage_policy?: string;
  max_bytes?: number; spooled?: boolean; workspace_allowed?: boolean;
}
export type ArtifactState = "pending_transfer" | "committed" | "replicated" | "failed" | "deleted";
export interface StorageArtifact {
  artifact_id: string; owner_node_id: string; origin_node_id: string; camera_id: string;
  kind: ContentKind; mime_type: string; size_bytes: number; sha256: string; created_at: number;
  updated_at: number; state: ArtifactState; location_id: string | null;
  requested_destination: DestinationRef; policy_revision: number; parent_id: string | null;
  metadata: Record<string, unknown>; retention: StorageRetention; replicas: DestinationRef[];
  owner_online: boolean | null; last_seen: number | null;
}
export interface StorageTransfer {
  transfer_id: string; artifact_id: string; location_id: string; offset: number; size_bytes: number;
  state: "receiving" | "verifying" | "committed" | "failed" | "cancelled";
  created_at: number; updated_at: number; error_code: string | null;
  direction?: "receiver" | "outbound"; requested_destination?: DestinationRef | null; actual_owner_node_id?: string | null;
}
export interface StoragePage<T> { items: T[]; next_cursor: string | null }
export interface ArtifactFilters { origin_node_id?: string; camera_id?: string; kind?: string; state?: string }
export interface MigrationInput { source_location_id: string; destination: DestinationRef; content_kinds?: ContentKind[]; remove_source: boolean }
export interface MigrationPreview {
  preview_id: string; expires_at: number; source_location_id: string; destination: DestinationRef;
  remove_source: boolean; item_count: number; total_bytes: number; can_start: boolean; blockers: string[];
  items: { artifact_id: string; name: string; kind: ContentKind; size_bytes: number; action: string; reason?: string }[];
}
export interface StorageMigration {
  migration_id: string; state: string; phase: string; completed_items: number; total_items: number;
  bytes_done: number; total_bytes: number; detail: string; remove_source: boolean;
  can_cancel?: boolean; can_resume?: boolean;
}
