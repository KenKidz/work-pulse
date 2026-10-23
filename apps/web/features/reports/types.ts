/**
 * Re-exports of the generated reports wire types — feature code imports from
 * here, never from the generated schemas file directly (overtime/leave
 * pattern). The tab vocabulary and the shared query state are local because
 * they are surface concerns, not part of the API contract.
 */

import type {
  AttendanceReport,
  HeadcountReport,
  LeaveReport,
  OvertimeReport,
  ReportDimension,
} from "@workpulse/api-client/index.schemas";

export type {
  AttendanceReport,
  AttendanceReportRow,
  GetAttendanceReportParams,
  GetHeadcountReportParams,
  GetLeaveReportParams,
  GetOvertimeReportParams,
  HeadcountReport,
  HeadcountReportRow,
  LeaveReport,
  LeaveReportRow,
  OvertimeReport,
  OvertimeReportRow,
  ReportDimension,
  ReportGroup,
  ReportInterval,
} from "@workpulse/api-client/index.schemas";

/** The four report tabs, in display order (the flagship overtime first). */
export type ReportKind = "overtime" | "attendance" | "leave" | "headcount";

export const REPORT_KINDS: readonly ReportKind[] = ["overtime", "attendance", "leave", "headcount"];

/** Every dimension the four reports accept; `leave_type` is leave-only. */
export const REPORT_DIMENSIONS: readonly ReportDimension[] = [
  "department",
  "location",
  "position",
  "seniority",
];

export const LEAVE_REPORT_DIMENSIONS: readonly ReportDimension[] = [
  ...REPORT_DIMENSIONS,
  "leave_type",
];

/** The shared query state the filter bar owns and the hooks consume. */
export type ReportFiltersState = {
  from: string;
  to: string;
  interval: "day" | "week" | "month" | "total";
  groupBy: ReportDimension[];
  departmentId?: string;
  locationId?: string;
  positionId?: string;
};

/** The wire params every report endpoint shares. */
export type ReportParams = {
  from: string;
  to: string;
  group_by: string;
  interval: "day" | "week" | "month" | "total";
  department_id?: string;
  location_id?: string;
  position_id?: string;
};

/** The four response envelopes share the row-union shape the view normalizes. */
export type ReportPayload = OvertimeReport | AttendanceReport | LeaveReport | HeadcountReport;
