/**
 * Candidates tab shell. T6 lands the workspace and the Jobs surface; the
 * intake form, pipeline actions and hire dialog replace this panel in T7.
 */

import { Users } from "lucide-react";

import { EmptyState } from "@/components/empty-state";

export function CandidatesPanel() {
  return <EmptyState icon={Users} message="No candidates to show here yet." />;
}
