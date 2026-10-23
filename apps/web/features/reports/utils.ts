/**
 * Reports math and presentation helpers — pure functions only (unit-tested in
 * `tests/reports/utils.test.ts`).
 *
 * The API returns one row per bucket × group with metric values already
 * rounded (ADR 0017/0019). The web keeps those values exact as numbers for the
 * chart and formats them only at render time, so the chart and the table always
 * read the same row: `normalizeReport` is the single pivot both consume.
 */

import { dayjs } from "@/lib/datetime";

import { REPORT_DIMENSION_LABELS } from "./labels";
import {
  LEAVE_REPORT_DIMENSIONS,
  REPORT_DIMENSIONS,
  type AttendanceReport,
  type HeadcountReport,
  type LeaveReport,
  type OvertimeReport,
  type ReportDimension,
  type ReportFiltersState,
  type ReportGroup,
  type ReportInterval,
  type ReportKind,
  type ReportParams,
  type ReportPayload,
} from "./types";

// --- Filters ---------------------------------------------------------------

/** The server's UTC date as the `YYYY-MM-DD` string the API expects. */
export function todayISO(): string {
  return dayjs.utc().format("YYYY-MM-DD");
}

/** The workspace's initial window: the last six calendar months to today. */
export function defaultReportFilters(): ReportFiltersState {
  const today = dayjs.utc();
  return {
    from: today.startOf("month").subtract(5, "month").format("YYYY-MM-DD"),
    to: today.format("YYYY-MM-DD"),
    interval: "month",
    groupBy: ["department"],
  };
}

/** Filter lookups page at the API maximum; these bound the extra requests. */
export const REPORT_FILTER_OPTION_PAGE_SIZE = 100;
export const REPORT_FILTER_OPTION_MAX_PAGES = 20;

/**
 * The page numbers to fetch after page 1. The API clamps every list to 100
 * rows, so the filter selects must page or silently drop real dimensions.
 * `totalPages` is capped so a pathological org list cannot fan out unbounded.
 */
export function filterOptionPageNumbers(totalPages: number): number[] {
  const capped = Math.min(Math.max(totalPages, 1), REPORT_FILTER_OPTION_MAX_PAGES);
  return Array.from({ length: capped - 1 }, (_, index) => index + 2);
}

/** The dimensions a report kind accepts (`leave_type` is leave-only). */
export function reportKindDimensions(kind: ReportKind): readonly ReportDimension[] {
  return kind === "leave" ? LEAVE_REPORT_DIMENSIONS : REPORT_DIMENSIONS;
}

/** Keep only dimensions this report accepts; never return an empty list. */
export function effectiveGroupBy(
  kind: ReportKind,
  groupBy: readonly ReportDimension[],
): ReportDimension[] {
  const allowed = reportKindDimensions(kind);
  const filtered = groupBy.filter((dimension) => allowed.includes(dimension));
  if (filtered.length === 0) return ["department"];
  return filtered.slice(0, 3);
}

/** The exact wire params a tab sends (empty filter ids are omitted). */
export function toReportParams(filters: ReportFiltersState, kind: ReportKind): ReportParams {
  return {
    from: filters.from,
    to: filters.to,
    group_by: effectiveGroupBy(kind, filters.groupBy).join(","),
    interval: filters.interval,
    department_id: filters.departmentId,
    location_id: filters.locationId,
    position_id: filters.positionId,
  };
}

/**
 * Add or remove a grouping dimension, preserving selection order. One
 * dimension stays selected at all times, and at most three are allowed (the
 * API's pinned limit), so the controls cannot build an invalid query.
 */
export function toggleGroupDimension(
  current: readonly ReportDimension[],
  dimension: ReportDimension,
  allowed: readonly ReportDimension[] = REPORT_DIMENSIONS,
): ReportDimension[] {
  if (!allowed.includes(dimension)) return [...current];
  if (current.includes(dimension)) {
    if (current.length === 1) return [...current];
    return current.filter((item) => item !== dimension);
  }
  if (current.length >= 3) return [...current];
  return [...current, dimension];
}

/** `Department · Location` — the table's group column header. */
export function groupByLabel(dimensions: readonly ReportDimension[]): string {
  return dimensions.map((dimension) => REPORT_DIMENSION_LABELS[dimension]).join(" · ");
}

/** The visible group path for one row (`Backend · Ho Chi Minh Office`). */
export function groupPath(groups: readonly ReportGroup[]): string {
  return groups.map((group) => group.label || group.key).join(" · ");
}

// --- Metrics ---------------------------------------------------------------

export type MetricFormat = "hours" | "count" | "minutes" | "rate" | "decimal1" | "decimal2";

export type ReportMetric = {
  key: string;
  label: string;
  format: MetricFormat;
};

const OVERTIME_METRICS: readonly ReportMetric[] = [
  { key: "verified_hours", label: "Verified hours", format: "hours" },
  { key: "verified_request_count", label: "Requests", format: "count" },
  { key: "verified_day_count", label: "Verified days", format: "count" },
  { key: "active_employee_count", label: "Active employees", format: "count" },
  { key: "hours_per_employee", label: "Hours per employee", format: "decimal2" },
];

const ATTENDANCE_METRICS: readonly ReportMetric[] = [
  { key: "checkin_count", label: "Check-ins", format: "count" },
  { key: "worked_day_count", label: "Worked days", format: "count" },
  { key: "worked_minutes", label: "Worked time", format: "minutes" },
  { key: "late_count", label: "Late check-ins", format: "count" },
  { key: "late_rate", label: "Late rate", format: "rate" },
  { key: "avg_minutes_late", label: "Avg minutes late", format: "decimal1" },
];

const LEAVE_METRICS: readonly ReportMetric[] = [
  { key: "approved_day_count", label: "Approved days", format: "count" },
  { key: "approved_request_count", label: "Approved requests", format: "count" },
];

const HEADCOUNT_METRICS: readonly ReportMetric[] = [
  { key: "headcount", label: "Headcount", format: "count" },
  { key: "joiners", label: "Joiners", format: "count" },
  { key: "leavers", label: "Leavers", format: "count" },
];

/** The one metric each chart plots (the table below carries the rest). */
export const CHART_METRIC_KEYS: Record<ReportKind, string> = {
  overtime: "verified_hours",
  attendance: "worked_minutes",
  leave: "approved_day_count",
  headcount: "headcount",
};

function metricsFor(kind: ReportKind): readonly ReportMetric[] {
  switch (kind) {
    case "overtime":
      return OVERTIME_METRICS;
    case "attendance":
      return ATTENDANCE_METRICS;
    case "leave":
      return LEAVE_METRICS;
    case "headcount":
      return HEADCOUNT_METRICS;
  }
}

function metricFor(kind: ReportKind, key: string): ReportMetric {
  return (
    metricsFor(kind).find((metric) => metric.key === key) ??
    metricsFor(kind)[0] ?? { key, label: key, format: "count" }
  );
}

// --- Normalization (chart + table read the same rows) ----------------------

type AggregateRow = {
  bucket: string | null;
  groups: ReportGroup[];
};

type ValuesFor<TRow> = (row: TRow) => Record<string, number>;

export type NormalizedReportRow = {
  id: string;
  bucket: string | null;
  groups: ReportGroup[];
  values: Record<string, number>;
};

export type ReportChartSeries = {
  key: string;
  label: string;
};

export type ReportChartPoint = {
  label: string;
  [series: string]: string | number | undefined;
};

export type ReportChart = {
  metric: ReportMetric;
  points: ReportChartPoint[];
  series: ReportChartSeries[];
};

export type NormalizedReport = {
  metrics: ReportMetric[];
  rows: NormalizedReportRow[];
  chart: ReportChart;
  groupColumnLabel: string;
};

function seriesKey(groups: readonly ReportGroup[]): string {
  return groups.map((group) => group.key).join("|");
}

function bucketOrder(bucket: string | null): string {
  return bucket ?? "9999-12-31";
}

function buildNormalized<TRow extends AggregateRow>(
  kind: ReportKind,
  rows: readonly TRow[],
  interval: ReportInterval,
  groupColumnLabel: string,
  valuesFor: ValuesFor<TRow>,
): NormalizedReport {
  const metrics = [...metricsFor(kind)];
  const chartMetric = metricFor(kind, CHART_METRIC_KEYS[kind]);

  const normalized = rows
    .map((row) => ({
      id: `${row.bucket ?? "total"}::${seriesKey(row.groups)}`,
      bucket: row.bucket,
      groups: row.groups,
      values: valuesFor(row),
    }))
    .sort(
      (left, right) =>
        bucketOrder(left.bucket).localeCompare(bucketOrder(right.bucket)) ||
        groupPath(left.groups).localeCompare(groupPath(right.groups)),
    );

  const series = new Map<string, ReportChartSeries>();
  for (const row of normalized) {
    const key = seriesKey(row.groups);
    if (!series.has(key)) {
      series.set(key, { key, label: groupPath(row.groups) });
    }
  }

  const pointsByBucket = new Map<string, ReportChartPoint>();
  for (const row of normalized) {
    const bucketKey = row.bucket ?? "total";
    let point = pointsByBucket.get(bucketKey);
    if (point === undefined) {
      point = { label: formatBucketAxis(row.bucket, interval) };
      pointsByBucket.set(bucketKey, point);
    }
    point[seriesKey(row.groups)] = row.values[chartMetric.key] ?? 0;
  }

  return {
    metrics,
    rows: normalized,
    chart: {
      metric: chartMetric,
      points: [...pointsByBucket.values()],
      series: [...series.values()],
    },
    groupColumnLabel,
  };
}

/** Pivot one report envelope into the shared chart/table shape. */
export function normalizeReport(kind: ReportKind, report: ReportPayload): NormalizedReport {
  const groupColumnLabel = groupByLabel(report.group_by);

  switch (kind) {
    case "overtime": {
      const { rows, interval } = report as OvertimeReport;
      return buildNormalized(kind, rows, interval, groupColumnLabel, (row) => ({
        verified_hours: Number(row.verified_hours),
        verified_request_count: row.verified_request_count,
        verified_day_count: row.verified_day_count,
        active_employee_count: row.active_employee_count,
        hours_per_employee: Number(row.hours_per_employee),
      }));
    }
    case "attendance": {
      const { rows, interval } = report as AttendanceReport;
      return buildNormalized(kind, rows, interval, groupColumnLabel, (row) => ({
        checkin_count: row.checkin_count,
        worked_day_count: row.worked_day_count,
        worked_minutes: row.worked_minutes,
        late_count: row.late_count,
        late_rate: Number(row.late_rate),
        avg_minutes_late: Number(row.avg_minutes_late),
      }));
    }
    case "leave": {
      const { rows, interval } = report as LeaveReport;
      return buildNormalized(kind, rows, interval, groupColumnLabel, (row) => ({
        approved_day_count: row.approved_day_count,
        approved_request_count: row.approved_request_count,
      }));
    }
    case "headcount": {
      const { rows, interval } = report as HeadcountReport;
      return buildNormalized(kind, rows, interval, groupColumnLabel, (row) => ({
        headcount: row.headcount,
        joiners: row.joiners,
        leavers: row.leavers,
      }));
    }
  }
}

// --- Formatting ------------------------------------------------------------

const integerFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});
const oneDecimalFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const twoDecimalFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const percentFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const percentAxisFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 0,
});

/** `450` → `7h 30m`; whole hours drop the minute part. */
export function formatMinutes(value: number): string {
  const total = Math.max(0, Math.round(value));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (hours === 0) return `${minutes}m`;
  if (minutes === 0) return `${hours}h`;
  return `${hours}h ${minutes}m`;
}

/** The table's metric text; the column header carries the unit. */
export function formatMetricValue(value: number, format: MetricFormat): string {
  switch (format) {
    case "hours":
      return twoDecimalFormatter.format(value);
    case "count":
      return integerFormatter.format(value);
    case "minutes":
      return formatMinutes(value);
    case "rate":
      return percentFormatter.format(value);
    case "decimal1":
      return oneDecimalFormatter.format(value);
    case "decimal2":
      return twoDecimalFormatter.format(value);
  }
}

/** A compact chart axis/tooltip label. */
export function formatMetricAxis(value: number, format: MetricFormat): string {
  switch (format) {
    case "minutes": {
      if (value < 60) return `${integerFormatter.format(value)}m`;
      return `${oneDecimalFormatter.format(value / 60)}h`;
    }
    case "rate":
      return percentAxisFormatter.format(value);
    default:
      return formatMetricValue(value, format);
  }
}

/** The table's period cell (`Mar 2026`, `Week of Mar 2, 2026`, `Total`). */
export function formatBucket(bucket: string | null, interval: ReportInterval): string {
  if (bucket === null || interval === "total") return "Total";
  switch (interval) {
    case "month":
      return dayjs(bucket).format("MMM YYYY");
    case "week":
      return `Week of ${dayjs(bucket).format("MMM D, YYYY")}`;
    case "day":
      return dayjs(bucket).format("MMM D, YYYY");
    default:
      return "Total";
  }
}

/** The chart's x-axis tick (`Mar 2026`, `Mar 2`, `Total`). */
export function formatBucketAxis(bucket: string | null, interval: ReportInterval): string {
  if (bucket === null || interval === "total") return "Total";
  if (interval === "month") return dayjs(bucket).format("MMM YYYY");
  if (interval === "week") return dayjs(bucket).format("MMM D");
  return dayjs(bucket).format("MMM D");
}

/** `Mar 1, 2026 – Mar 31, 2026` (the filter summary). */
export function formatRange(from: string, to: string): string {
  return `${dayjs(from).format("MMM D, YYYY")} – ${dayjs(to).format("MMM D, YYYY")}`;
}
