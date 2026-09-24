import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import timezone from "dayjs/plugin/timezone";

dayjs.extend(utc);
dayjs.extend(timezone);

export { dayjs };

/**
 * Format a UTC ISO 8601 timestamp in a target timezone.
 * The API contract is always UTC ISO strings; conversion happens only here,
 * at render time.
 */
export function formatDateTime(iso: string, timeZone: string, format = "YYYY-MM-DD HH:mm"): string {
  return dayjs.utc(iso).tz(timeZone).format(format);
}
