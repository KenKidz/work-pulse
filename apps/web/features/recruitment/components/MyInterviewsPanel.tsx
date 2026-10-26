/**
 * "My interviews" tab shell. T6 lands the workspace shell and the Jobs
 * surface; the assigned-interview list and feedback dialog replace this panel
 * in T8.
 */

import { CalendarClock } from "lucide-react";

import { EmptyState } from "@/components/empty-state";

export function MyInterviewsPanel() {
  return <EmptyState icon={CalendarClock} message="No interviews to show here yet." />;
}
