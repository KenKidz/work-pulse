/**
 * Reports math — filter/param mapping, the chart/table pivot and every
 * formatter. Pure functions, no React, no network.
 */

import { describe, expect, it } from "vitest";

import { dayjs } from "@/lib/datetime";
import type {
  AttendanceReport,
  HeadcountReport,
  LeaveReport,
  OvertimeReport,
  ReportFiltersState,
} from "@/features/reports/types";
import {
  defaultReportFilters,
  effectiveGroupBy,
  filterOptionPageNumbers,
  formatBucket,
  formatMetricAxis,
  formatMetricValue,
  formatMinutes,
  groupByLabel,
  groupPath,
  normalizeReport,
  toReportParams,
  toggleGroupDimension,
} from "@/features/reports/utils";
import { intervalLabel } from "@/features/reports/labels";

const FILTERS: ReportFiltersState = {
  from: "2026-03-01",
  to: "2026-04-30",
  interval: "month",
  groupBy: ["department"],
};

function overtimeReport(): OvertimeReport {
  return {
    from: "2026-03-01",
    to: "2026-04-30",
    group_by: ["department"],
    interval: "month",
    rows: [
      {
        bucket: "2026-04-01",
        groups: [{ dimension: "department", key: "d2", label: "Frontend" }],
        verified_hours: "2.50",
        verified_request_count: 1,
        verified_day_count: 1,
        active_employee_count: 1,
        hours_per_employee: "2.50",
      },
      {
        bucket: "2026-03-01",
        groups: [{ dimension: "department", key: "d1", label: "Backend" }],
        verified_hours: "3.75",
        verified_request_count: 2,
        verified_day_count: 2,
        active_employee_count: 2,
        hours_per_employee: "1.88",
      },
    ],
  };
}

describe("filters and params", () => {
  it("defaults to the last six calendar months and monthly department rows", () => {
    const filters = defaultReportFilters();
    const today = dayjs.utc();

    expect(filters.to).toBe(today.format("YYYY-MM-DD"));
    expect(filters.from).toBe(today.startOf("month").subtract(5, "month").format("YYYY-MM-DD"));
    expect(filters.interval).toBe("month");
    expect(filters.groupBy).toEqual(["department"]);
  });

  it("serializes the grouping in selection order and omits empty filters", () => {
    const params = toReportParams(
      {
        ...FILTERS,
        groupBy: ["location", "department"],
      },
      "overtime",
    );
    expect(params.group_by).toBe("location,department");
    expect(params.department_id).toBeUndefined();
    expect(params.location_id).toBeUndefined();
    expect(params.position_id).toBeUndefined();
  });

  it("drops leave_type from non-leave reports and keeps it for leave", () => {
    const filters: ReportFiltersState = { ...FILTERS, groupBy: ["leave_type"] };
    expect(toReportParams(filters, "overtime").group_by).toBe("department");
    expect(toReportParams(filters, "leave").group_by).toBe("leave_type");
  });

  it("toggles dimensions in order, never below one or above three", () => {
    expect(toggleGroupDimension(["department"], "location")).toEqual(["department", "location"]);
    expect(toggleGroupDimension(["department", "location"], "department")).toEqual(["location"]);
    expect(toggleGroupDimension(["department"], "department")).toEqual(["department"]);
    expect(toggleGroupDimension(["department", "location", "position"], "seniority")).toEqual([
      "department",
      "location",
      "position",
    ]);
    expect(toggleGroupDimension(["department"], "leave_type")).toEqual(["department"]);
    expect(toggleGroupDimension(["leave_type"], "leave_type", ["leave_type"])).toEqual([
      "leave_type",
    ]);
  });

  it("pages filter options up to the display cap", () => {
    expect(filterOptionPageNumbers(0)).toEqual([]);
    expect(filterOptionPageNumbers(1)).toEqual([]);
    expect(filterOptionPageNumbers(2)).toEqual([2]);
    expect(filterOptionPageNumbers(30)).toEqual(
      Array.from({ length: 19 }, (_, index) => index + 2),
    );
  });

  it("labels a group column in dimension order", () => {
    expect(groupByLabel(["department", "location", "seniority"])).toBe(
      "Department · Location · Seniority",
    );
    expect(intervalLabel("total")).toBe("Whole range");
  });
});

describe("normalizeReport", () => {
  it("pivots overtime rows into one chart point per bucket and one series per group", () => {
    const normalized = normalizeReport("overtime", overtimeReport());

    expect(normalized.groupColumnLabel).toBe("Department");
    expect(normalized.rows.map((row) => row.bucket)).toEqual(["2026-03-01", "2026-04-01"]);
    expect(normalized.chart.metric.key).toBe("verified_hours");
    expect(normalized.chart.series).toEqual([
      { key: "d1", label: "Backend" },
      { key: "d2", label: "Frontend" },
    ]);
    expect(normalized.chart.points).toEqual([
      { label: "Mar 2026", d1: 3.75 },
      { label: "Apr 2026", d2: 2.5 },
    ]);
    expect(normalized.metrics.map((metric) => metric.key)).toContain("hours_per_employee");
  });

  it("normalizes attendance, leave and headcount with their own chart metric", () => {
    const attendance: AttendanceReport = {
      from: "2026-03-01",
      to: "2026-03-31",
      group_by: ["location"],
      interval: "month",
      rows: [
        {
          bucket: "2026-03-01",
          groups: [{ dimension: "location", key: "l1", label: "Ho Chi Minh Office" }],
          checkin_count: 12,
          worked_day_count: 11,
          worked_minutes: 4800,
          late_count: 3,
          late_rate: "0.2500",
          avg_minutes_late: "12.5",
        },
      ],
    };
    const attendancePivot = normalizeReport("attendance", attendance);
    expect(attendancePivot.chart.metric.key).toBe("worked_minutes");
    expect(attendancePivot.chart.points).toEqual([{ label: "Mar 2026", l1: 4800 }]);

    const leave: LeaveReport = {
      from: "2026-03-01",
      to: "2026-03-31",
      group_by: ["leave_type"],
      interval: "month",
      rows: [
        {
          bucket: "2026-03-01",
          groups: [{ dimension: "leave_type", key: "annual", label: "Annual" }],
          approved_day_count: 4,
          approved_request_count: 2,
        },
      ],
    };
    const leavePivot = normalizeReport("leave", leave);
    expect(leavePivot.chart.metric.key).toBe("approved_day_count");
    expect(leavePivot.groupColumnLabel).toBe("Leave type");
    expect(leavePivot.rows[0]?.values.approved_day_count).toBe(4);

    const headcount: HeadcountReport = {
      from: "2026-03-01",
      to: "2026-03-31",
      group_by: ["department", "seniority"],
      interval: "total",
      rows: [
        {
          bucket: null,
          groups: [
            { dimension: "department", key: "d1", label: "Backend" },
            { dimension: "seniority", key: "1_3y", label: "1–<3 years" },
          ],
          headcount: 8,
          joiners: 1,
          leavers: 1,
        },
      ],
    };
    const headcountPivot = normalizeReport("headcount", headcount);
    expect(headcountPivot.chart.metric.key).toBe("headcount");
    expect(headcountPivot.chart.points).toEqual([{ label: "Total", "d1|1_3y": 8 }]);
    expect(headcountPivot.groupColumnLabel).toBe("Department · Seniority");
  });

  it("keeps selection order when a report groups by multiple dimensions", () => {
    const report: OvertimeReport = {
      ...overtimeReport(),
      group_by: ["location", "department"],
      rows: [
        {
          bucket: "2026-04-01",
          groups: [
            { dimension: "location", key: "l1", label: "Berlin Office" },
            { dimension: "department", key: "d1", label: "Backend" },
          ],
          verified_hours: "1.00",
          verified_request_count: 1,
          verified_day_count: 1,
          active_employee_count: 1,
          hours_per_employee: "1.00",
        },
      ],
    };
    const normalized = normalizeReport("overtime", report);
    expect(normalized.groupColumnLabel).toBe("Location · Department");
    expect(groupPath(normalized.rows[0]?.groups ?? [])).toBe("Berlin Office · Backend");
  });
});

describe("formatters", () => {
  it("formats every metric vocabulary and bucket shape", () => {
    expect(formatMetricValue(3.75, "hours")).toBe("3.75");
    expect(formatMetricValue(3.75, "decimal2")).toBe("3.75");
    expect(formatMetricValue(128, "count")).toBe("128");
    expect(formatMetricValue(450, "minutes")).toBe("7h 30m");
    expect(formatMetricValue(480, "minutes")).toBe("8h");
    expect(formatMetricValue(30, "minutes")).toBe("30m");
    expect(formatMetricValue(0.2567, "rate")).toBe("25.7%");
    expect(formatMetricValue(12.54, "decimal1")).toBe("12.5");
    expect(formatMetricAxis(480, "minutes")).toBe("8.0h");
    expect(formatMetricAxis(45, "minutes")).toBe("45m");
    expect(formatMetricAxis(0.2567, "rate")).toBe("26%");
    expect(formatMinutes(0)).toBe("0m");

    expect(formatBucket("2026-03-01", "month")).toBe("Mar 2026");
    expect(formatBucket("2026-03-02", "week")).toBe("Week of Mar 2, 2026");
    expect(formatBucket("2026-03-02", "day")).toBe("Mar 2, 2026");
    expect(formatBucket(null, "total")).toBe("Total");
  });

  it("keeps only dimensions a report accepts", () => {
    expect(effectiveGroupBy("overtime", ["leave_type"])).toEqual(["department"]);
    expect(effectiveGroupBy("leave", ["leave_type", "department"])).toEqual([
      "leave_type",
      "department",
    ]);
    expect(
      effectiveGroupBy("headcount", ["department", "location", "position", "seniority"]),
    ).toEqual(["department", "location", "position"]);
  });
});
