/**
 * Timestamp rendering for the inbox. The API contract is UTC ISO 8601;
 * `Intl` renders it in the browser's local zone and active locale.
 */

/** "Oct 21, 09:42" — enough precision for a person-addressed inbox row. */
export function formatNotificationTime(iso: string, locale = "en-US"): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}
