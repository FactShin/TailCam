import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "./storage";
import type { ArtifactFilters } from "../storageTypes";

export function useStoragePolicy() {
  return useQuery({queryKey: ["storage-policy"], queryFn: api.getStoragePolicy, retry: false, refetchInterval: 30000});
}
export function useSaveStoragePolicy() {
  const qc = useQueryClient();
  return useMutation({mutationFn: api.saveStoragePolicy, onSuccess: async data => {
    await qc.cancelQueries({queryKey: ["storage-policy"]});
    qc.setQueryData(["storage-policy"], data);
  }});
}
export function useStorageLocations() {
  return useQuery({queryKey: ["storage-locations"], queryFn: api.getStorageLocations, retry: false, refetchInterval: 15000});
}
export function useStorageDestinations() {
  return useQuery({queryKey: ["storage-destinations"], queryFn: api.getStorageDestinations, retry: false, refetchInterval: 30000});
}
export function useRegisterStorageLocation() {
  const qc = useQueryClient();
  return useMutation({mutationFn: api.registerStorageLocation, onSuccess: () => {
    qc.invalidateQueries({queryKey: ["storage-locations"]});
    qc.invalidateQueries({queryKey: ["storage-destinations"]});
  }});
}
export function useUpdateStorageLocation() {
  const qc = useQueryClient();
  return useMutation({mutationFn: api.updateStorageLocation, onSuccess: () => {
    qc.invalidateQueries({queryKey: ["storage-locations"]});
    qc.invalidateQueries({queryKey: ["storage-destinations"]});
  }});
}
export const useStorageAdmission = () => useMutation({mutationFn: api.checkStorageAdmission});
export function useArtifacts(filters: ArtifactFilters) {
  return useInfiniteQuery({queryKey: ["storage-artifacts", filters], initialPageParam: undefined as string | undefined,
    queryFn: ({pageParam}) => api.getArtifacts(filters, pageParam), getNextPageParam: page => page.next_cursor ?? undefined,
    retry: false, refetchInterval: 15000});
}
export function useUpdateArtifactRetention() {
  const qc = useQueryClient();
  return useMutation({mutationFn: api.updateArtifactRetention, onSuccess: async () => {
    await qc.cancelQueries({queryKey: ["storage-artifacts"]});
    await qc.invalidateQueries({queryKey: ["storage-artifacts"]});
  }});
}
export function useStorageTransfers() {
  return useInfiniteQuery({queryKey: ["storage-transfers"], initialPageParam: undefined as string | undefined,
    queryFn: ({pageParam}) => api.getTransfers(pageParam), getNextPageParam: page => page.next_cursor ?? undefined,
    retry: false, refetchInterval: 5000});
}
export function useRetryStorageTransfer() {
  const qc = useQueryClient();
  return useMutation({mutationFn: api.retryStorageTransfer, onSuccess: () => {
    qc.invalidateQueries({queryKey: ["storage-transfers"]});
    qc.invalidateQueries({queryKey: ["storage-artifacts"]});
  }});
}
export const usePreviewMigration = () => useMutation({mutationFn: api.previewMigration});
export function useStorageMigrations() {
  return useQuery({queryKey: ["storage-migrations"], queryFn: api.getMigrations, retry: false, refetchInterval: 5000});
}
export function useStartMigration() {
  const qc = useQueryClient();
  return useMutation({mutationFn: api.startMigration, onSuccess: () => qc.invalidateQueries({queryKey: ["storage-migrations"]})});
}
export function useControlMigration() {
  const qc = useQueryClient();
  return useMutation({mutationFn: api.controlMigration, onSuccess: () => qc.invalidateQueries({queryKey: ["storage-migrations"]})});
}
