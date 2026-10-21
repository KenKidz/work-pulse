/**
 * Timestamp rendering for the inbox. The API contract is UTC ISO 8601;
 * `dayjs` converts to the browser's local zone at render time only.
 */

import { dayjs } from "@/lib/datetime";

/** "Oct 21, 09:42" — enough precision for a person-addressed inbox row. */
export function formatNotificationTime(iso: string): string {
  return dayjs(iso).format("MMM D, HH:mm");
}
