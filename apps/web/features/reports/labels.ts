/**
 * User-visible words for the Reports workspace — tab names, dimension names,
 * interval options, pinned error copy and empty-state invitations. Wire values
 * (`department`, `1_3y`, `reports_unavailable`, …) never reach the DOM
 * directly (workpulse-ui §6).
 */

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import type { ReportDimension, ReportInterval, ReportKind } from "./types";

export const REPORT_TAB_LABELS: Record<ReportKind, string> = {
  overtime: "Overtime",
  attendance: "Attendance",
  leave: "Leave",
  headcount: "Headcount",
};

export const REPORT_TAB_DESCRIPTIONS: Record<ReportKind, string> = {
  overtime: "Verified hours, claim counts and hours per active employee.",
  attendance: "Check-ins, worked time and lateness.",
  leave: "Approved leave days and requests.",
  headcount: "Active headcount at each bucket end, plus joiners and leavers.",
};

export const REPORT_EMPTY_MESSAGES: Record<ReportKind, string> = {
  overtime: "No verified overtime in this range. Claims appear here after HR verifies them.",
  attendance: "No attendance in this range. Check-ins appear here once they are recorded.",
  leave: "No approved leave in this range. Approved requests appear here.",
  headcount: "No headcount in this range — no employee was employed during these buckets.",
};

export const REPORT_DIMENSION_LABELS: Record<ReportDimension, string> = {
  department: "Department",
  location: "Location",
  position: "Position",
  seniority: "Seniority",
  leave_type: "Leave type",
};

export const INTERVAL_OPTIONS: readonly { value: ReportInterval; label: string }[] = [
  { value: "day", label: "Daily" },
  { value: "week", label: "Weekly" },
  { value: "month", label: "Monthly" },
  { value: "total", label: "Whole range" },
];

export function intervalLabel(interval: ReportInterval): string {
  return INTERVAL_OPTIONS.find((option) => option.value === interval)?.label ?? interval;
}

/** Closed vocabulary mirrored from the API (`SENIORITY_BANDS`). */
export const SENIORITY_BAND_LABELS: Record<string, string> = {
  lt_1y: "< 1 year",
  "1_3y": "1–<3 years",
  "3_5y": "3–<5 years",
  "5y_plus": "5+ years",
  unknown: "Unknown",
};

/** The pinned failure codes the reports surface can show. */
export function reportErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "reports_unavailable") {
      return "Reports are temporarily unavailable because the analytics store is not answering. Try again in a moment.";
    }
    if (error.status === 403 || error.code === "forbidden") {
      return "You do not have access to reports.";
    }
    if (error.status === 422 || error.code === "validation_error") {
      return "These report filters are not valid. Check the date range and grouping.";
    }
    return error.message || "The report could not be loaded. Please try again.";
  }
  return "The report could not be loaded. Please try again.";
}

/** A denied call is not retryable; a store hiccup is. */
export function isReportErrorRetryable(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status !== 403 && error.code !== "forbidden";
  }
  return true;
}
