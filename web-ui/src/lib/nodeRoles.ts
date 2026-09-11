import type { NodeRole } from "../types";

export const NODE_ROLE_LABELS: Record<NodeRole, string> = {
  capture: "Camera capture",
  storage: "Storage",
  analysis: "AI analysis",
  training: "Training",
};

export function nodeRoleSummary(roles: string[] | null | undefined): string {
  if (roles == null) return "Roles not reported";
  if (roles.length === 0) return "Hub · view and control";
  return roles.map((role) => NODE_ROLE_LABELS[role as NodeRole] ?? role).join(" · ");
}
