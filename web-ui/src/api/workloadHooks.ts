import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JobFilters, SupervisionRecord } from "../workloadTypes";
import * as api from "./workloads";

export function usePlacementPolicy() {
  return useQuery({ queryKey: ["workload-policy"], queryFn: api.getPlacementPolicy, retry: false, refetchInterval: 30000 });
}
export function useSavePlacementPolicy() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.savePlacementPolicy, onSuccess: async data => {
    await qc.cancelQueries({ queryKey: ["workload-policy"] });
    qc.setQueryData(["workload-policy"], data);
  }});
}
export const useWorkloadWorkers = () => useQuery({ queryKey: ["workload-workers"], queryFn: api.getWorkloadWorkers, retry: false, refetchInterval: 15000 });
export const useWorkloadProviders = () => useQuery({ queryKey: ["workload-providers"], queryFn: api.getWorkloadProviders, retry: false, refetchInterval: 30000 });
export function useSaveWorkloadProvider() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.saveWorkloadProvider, onSuccess: async () => {
    await qc.cancelQueries({ queryKey: ["workload-providers"] });
    await qc.invalidateQueries({ queryKey: ["workload-providers"] });
  }});
}
export function useWorkloadJobs(filters: JobFilters, enabled = true) {
  return useInfiniteQuery({ queryKey: ["workload-jobs", filters], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.getWorkloadJobs(filters, pageParam),
    getNextPageParam: page => page.next_cursor ?? undefined, retry: false, refetchInterval: 5000, enabled });
}
export function useWorkloadJob(id: string) {
  return useQuery({ queryKey: ["workload-job", id], queryFn: () => api.getWorkloadJob(id), enabled: !!id,
    retry: false, refetchInterval: 5000 });
}
export function useControlWorkloadJob() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.controlWorkloadJob, onSuccess: async data => {
    await qc.cancelQueries({ queryKey: ["workload-jobs"] });
    await qc.cancelQueries({ queryKey: ["workload-job", data.job_id] });
    qc.setQueryData(["workload-job", data.job_id], data);
    await qc.invalidateQueries({ queryKey: ["workload-jobs"] });
    await qc.invalidateQueries({ queryKey: ["supervision"] });
  }});
}
export function useSupervisions() {
  return useInfiniteQuery({ queryKey: ["supervisions"], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => api.getSupervisions(pageParam), getNextPageParam: page => page.next_cursor ?? undefined,
    retry: false, refetchInterval: 5000 });
}
export const useSupervision = (id: string) => useQuery({ queryKey: ["supervision", id], queryFn: () => api.getSupervision(id), enabled: !!id, retry: false, refetchInterval: 5000 });
export const useDatasetRevision = (id: number) => useQuery({ queryKey: ["supervision-dataset", id], queryFn: () => api.getDatasetRevision(id), enabled: id > 0, retry: false });
export const useSupervisionReport = (id: string, enabled: boolean) => useQuery({ queryKey: ["supervision-report", id], queryFn: () => api.getSupervisionReport(id), enabled: !!id && enabled, retry: false });
async function receiveSupervision(qc: ReturnType<typeof useQueryClient>, data: SupervisionRecord) {
  await qc.cancelQueries({ queryKey: ["supervision", data.supervision_id] });
  await qc.cancelQueries({ queryKey: ["supervisions"] });
  qc.setQueryData(["supervision", data.supervision_id], data);
  await qc.invalidateQueries({ queryKey: ["supervisions"] });
  await qc.invalidateQueries({ queryKey: ["supervision-report", data.supervision_id] });
}
export function useCreateSupervision() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.createSupervision, onSuccess: data => receiveSupervision(qc, data) });
}
export function useSubmitExperiment() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.submitExperiment, onSuccess: data => receiveSupervision(qc, data) });
}
export function useControlSupervision() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.controlSupervision, onSuccess: data => receiveSupervision(qc, data) });
}
