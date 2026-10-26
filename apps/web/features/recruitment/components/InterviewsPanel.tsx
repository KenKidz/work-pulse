/**
 * Interviews tab shell. T6 lands the workspace and the Jobs surface; the
 * schedule/reschedule/cancel and feedback surfaces replace this panel in T8.
 */

import { CalendarClock } from "lucide-react";

import { EmptyState } from "@/components/empty-state";

export function InterviewsPanel() {
  return <EmptyState icon={CalendarClock} message="No interviews to show here yet." />;
}
