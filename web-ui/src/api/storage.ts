import { jsonFetch } from "./client";
import type { AdmissionInput, ArtifactFilters, LocationInput, MigrationInput, MigrationPreview, StorageAdmission, StorageArtifact, StorageDestination, StorageLocation, StorageMigration, StoragePage, StoragePolicyStatus, StorageRetention, StorageTransfer, UnifiedStoragePolicy } from "../storageTypes";

const BASE = "/api/v1/storage";
const body = (value: unknown, method = "POST") => ({ method, body: JSON.stringify(value) });
const query = (values: Record<string, string | undefined>) => {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
  return params.size ? `?${params}` : "";
};
export const getStoragePolicy = () => jsonFetch<StoragePolicyStatus>(`${BASE}/policy`);
export const saveStoragePolicy = (value: { expected_revision: number; policy: UnifiedStoragePolicy }) => jsonFetch<StoragePolicyStatus>(`${BASE}/policy`, body(value, "PATCH"));
export const getStorageLocations = () => jsonFetch<{items: StorageLocation[]}>(`${BASE}/locations`);
export const getStorageDestinations = () => jsonFetch<{items: StorageDestination[]}>(`${BASE}/destinations`);
export const registerStorageLocation = (value: LocationInput) => jsonFetch<StorageLocation>(`${BASE}/locations`, body(value));
export const updateStorageLocation = ({id, ...value}: {id: string; label?: string; quota_bytes?: number; reserve_bytes?: number; make_default?: boolean}) => jsonFetch<StorageLocation>(`${BASE}/locations/${encodeURIComponent(id)}`, body(value, "PATCH"));
export const checkStorageAdmission = (value: AdmissionInput) => jsonFetch<StorageAdmission>(`${BASE}/admission`, body(value));
export const getArtifacts = (filters: ArtifactFilters, cursor?: string) => jsonFetch<StoragePage<StorageArtifact>>(`/api/v1/fleet/artifacts${query({...filters, cursor})}`);
export const getTransfers = (cursor?: string) => jsonFetch<StoragePage<StorageTransfer>>(`/api/v1/transfers${query({cursor})}`);
export const retryStorageTransfer = (id: string) => jsonFetch<number>(`/api/v1/transfers/${encodeURIComponent(id)}/retry`, body({}));
export const previewMigration = (value: MigrationInput) => jsonFetch<MigrationPreview>(`${BASE}/migrations/preview`, body(value));
export const getMigrations = () => jsonFetch<{items: StorageMigration[]}>(`${BASE}/migrations`);
export const startMigration = (preview_id: string) => jsonFetch<StorageMigration>(`${BASE}/migrations`, body({preview_id}));
export const controlMigration = ({id, action}: {id: string; action: "cancel" | "resume"}) => jsonFetch<StorageMigration>(`${BASE}/migrations/${encodeURIComponent(id)}/${action}`, body({}));
// Never use a server-provided URL or filesystem path as a download target.
export const artifactContentUrl = (id: string) => `/api/v1/artifacts/${encodeURIComponent(id)}/content`;
export const updateArtifactRetention = ({id, retention}: {id: string; retention: StorageRetention}) => jsonFetch<StorageArtifact>(`/api/v1/artifacts/${encodeURIComponent(id)}/retention`, body(retention, "PATCH"));
