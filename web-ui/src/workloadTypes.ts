export const WORKLOAD_TASKS = ["live_detection", "motion_description", "printer_analysis", "timelapse_encode", "timelapse_interpolate", "labeling", "training", "speech_recognition", "conversation", "speech_synthesis"] as const;
export type WorkloadTask = typeof WORKLOAD_TASKS[number];
export const TASK_LABELS: Record<WorkloadTask, string> = {
  live_detection: "Live detection", motion_description: "Motion descriptions", printer_analysis: "Printer analysis",
  timelapse_encode: "Timelapse encoding", timelapse_interpolate: "Frame interpolation", labeling: "Labeling", training: "Training",
  speech_recognition: "Speech recognition", conversation: "Conversation", speech_synthesis: "Speech synthesis",
};
export interface WorkloadBudget {
  cpu_threads: number; cpu_seconds: number; memory_bytes: number; gpu_slots: number;
  workspace_bytes: number; output_bytes: number; wall_seconds: number;
  cancel_grace_seconds: number; require_hard_memory_limit: boolean;
}
export interface WorkerTarget { node_id: string | null; provider_id: string | null; model: string }
export interface TaskRoute {
  mode: "manual" | "auto"; target: WorkerTarget | null; approved_node_ids: string[];
  fallback_targets: WorkerTarget[]; budget: WorkloadBudget;
}
export interface PlacementPolicy { revision: number; routes: Partial<Record<WorkloadTask, TaskRoute>> }
export interface PlacementPolicyStatus { source_node_id: string; policy: PlacementPolicy }
export interface WorkloadWorker {
  node_id: string; name: string; online: boolean; roles: string[];
  tasks: { task: WorkloadTask; state: "ready" | "unchecked" | "unavailable" | "disabled"; code: string; detail: string }[];
  queued: number; running: number; cpu_threads: number; memory_bytes: number;
  workspace_bytes: number; gpu_slots: number; latency_ms: number | null;
}
export interface WorkloadProvider { provider_id: string; name: string; kind: "ollama"; base_url: string; model: string; tasks: WorkloadTask[]; enabled: boolean }
export interface WorkloadPage<T> { items: T[]; next_cursor: string | null }
export interface JobFilters { task?: WorkloadTask; state?: JobState }
export interface PlacementPlan {
  task: WorkloadTask; policy_revision: number; mode: "manual" | "auto";
  requested_target: WorkerTarget; selected_target: WorkerTarget; fallback_targets: WorkerTarget[];
  reason: string; budget: WorkloadBudget;
}
export interface JobArtifactRef { artifact_id: string; owner_node_id: string; sha256: string; size_bytes: number; slot: string }
export type JobState = "queued" | "leased" | "running" | "committing" | "retry_wait" | "succeeded" | "failed" | "cancel_requested" | "cancelled" | "deadline_exceeded" | "waiting_for_worker";
export interface JobError { code: string; detail: string; retryable: boolean }
export interface JobProgress { fraction: number | null; epoch: number | null; message: string; metrics: Record<string, number> }
export interface JobStage {
  stage_id: string; task: WorkloadTask; state: JobState; attempt: number;
  worker_node_id: string | null; placement_plan: PlacementPlan | null; progress: JobProgress;
  heartbeat_at: number | null; started_at: number | null; ended_at: number | null;
  error: JobError | null; outputs: JobArtifactRef[]; result: Record<string, unknown>;
}
export interface WorkloadJob {
  job_id: string; coordinator_node_id: string; origin_node_id: string; task: WorkloadTask;
  state: JobState; revision: number; created_at: number; updated_at: number; deadline_at: number;
  started_at: number | null; ended_at: number | null; requested_target: WorkerTarget | null;
  actual_target: WorkerTarget | null; priority: number; stages: JobStage[]; error: JobError | null;
  cancel_requested: boolean; allowed_actions: string[]; reference: Record<string, string>;
}
export interface IntegerRange { minimum: number; maximum: number }
export interface SuccessCriterion { metric: string; direction: "maximize" | "minimize"; threshold: number }
export interface TrainingObjective {
  name: string; task: "classification" | "detection"; dataset_id: number; dataset_revision: string;
  camera_ids: string[]; classes: string[]; success_criteria: SuccessCriterion[]; evaluation_reference: string;
}
export interface SupervisionPolicy {
  allowed_model_ids: number[]; allowed_worker_node_ids: string[]; epochs: IntegerRange; image_size: IntegerRange;
  allowed_seeds: number[]; max_experiments: number; total_wall_seconds: number; experiment_budget: WorkloadBudget;
  max_attempts: number; agent_timeout_seconds: number; permitted_actions: ("experiment" | "inspect" | "stop" | "finish")[];
  activation_enabled: false;
}
export interface SupervisionCreate { objective: TrainingObjective; policy: SupervisionPolicy }
export interface ExperimentRequest {
  idempotency_key: string; base_model_id: number; worker_node_id: string; epochs: number;
  image_size: number; seed: number; reason: string;
}
export interface SupervisionExperiment {
  experiment_id: string; job_id: string; request: ExperimentRequest; created_at: number;
  reserved_wall_seconds: number; state: string; job: WorkloadJob | null; error_code: string | null;
}
export interface SupervisionRecord {
  supervision_id: string; node_id: string; owner: string; revision: number; objective: TrainingObjective;
  policy: SupervisionPolicy; created_at: number; updated_at: number;
  state: "active" | "stop_requested" | "stopped" | "completed" | "budget_exhausted" | "failed";
  agent_session_id: string | null; agent_heartbeat_at: number | null; agent_connected: boolean;
  last_decision: string; reason: string; next_check_at: number | null; current_job_id: string | null;
  remaining_experiments: number; remaining_wall_seconds: number; experiments: SupervisionExperiment[];
  allowed_actions: string[];
}
export interface DatasetRevision { dataset_id: number; revision: string; task: "classification" | "detection"; camera_ids: string[]; classes: string[] }
export interface SupervisionComparison {
  experiment_id: string; job_id: string; state: string; parameters: ExperimentRequest;
  dataset_id: number; dataset_revision: string; metrics: Record<string, unknown>; artifacts: JobArtifactRef[];
  provenance: { stage_id: string; worker_node_id: string | null; placement_plan: PlacementPlan | null;
    started_at: number | null; ended_at: number | null; result: Record<string, unknown> }[];
  reserved_wall_seconds: number; error_code: string | null;
}
export interface SupervisionReport {
  supervision: SupervisionRecord; comparison: SupervisionComparison[]; best_candidate: string | null;
  comparison_metric: string; activation_performed: false; limitations: string[];
}
