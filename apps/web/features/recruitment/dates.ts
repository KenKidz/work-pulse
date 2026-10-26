/**
 * Display formatting for recruitment data. Timestamps arrive as UTC ISO 8601
 * and render in the browser's local zone through `lib/datetime` (dayjs),
 * never through ad-hoc `new Date(...)` arithmetic.
 */

import { dayjs } from "@/lib/datetime";

/** A UTC ISO timestamp in the browser's local zone. */
export function formatRecruitmentTimestamp(value: string): string {
  return dayjs(value).format("MMM D, YYYY HH:mm");
}
