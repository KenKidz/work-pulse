/**
 * Reports error classification and retry policy. User-visible words live in
 * the `reports` message namespace; wire values (`department`, `1_3y`,
 * `reports_unavailable`, …) never reach the DOM directly.
 */

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

export type ReportErrorKey = "unavailable" | "forbidden" | "invalid" | "generic";

export function reportErrorMessage(
  error: unknown,
  t: (key: ReportErrorKey) => string,
): string {
  if (error instanceof ApiError) {
    if (error.code === "reports_unavailable") return t("unavailable");
    if (error.status === 403 || error.code === "forbidden") return t("forbidden");
    if (error.status === 422 || error.code === "validation_error") return t("invalid");
    return error.message || t("generic");
  }
  return t("generic");
}

/** A denied call is not retryable; a store hiccup is. */
export function isReportErrorRetryable(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status !== 403 && error.code !== "forbidden";
  }
  return true;
}
