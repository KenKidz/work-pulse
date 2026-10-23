/**
 * ReportsWorkspace — the four states, tab switching and filter → query-param
 * mapping. The generated api-client is mocked; the hooks, pivot and components
 * are the real thing (overtime/leave test pattern).
 */

import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useGetAttendanceReport,
  useGetHeadcountReport,
  useGetLeaveReport,
  useGetOvertimeReport,
  useListDepartments,
  useListLocations,
  useListPositions,
} from "@workpulse/api-client";

import { ReportsWorkspace } from "@/features/reports/components/ReportsWorkspace";
import type {
  AttendanceReport,
  HeadcountReport,
  LeaveReport,
  OvertimeReport,
  ReportPayload,
} from "@/features/reports/types";

import { apiError, renderWithClient, wireQuery } from "../leave/helpers";

vi.mock("@workpulse/api-client", () => ({
  useGetOvertimeReport: vi.fn(),
  useGetAttendanceReport: vi.fn(),
  useGetLeaveReport: vi.fn(),
  useGetHeadcountReport: vi.fn(),
  useListDepartments: vi.fn(),
  useListPositions: vi.fn(),
  useListLocations: vi.fn(),
  getListDepartmentsQueryOptions: vi.fn(),
  getListPositionsQueryOptions: vi.fn(),
  getListLocationsQueryOptions: vi.fn(),
}));

function queryState(payload: ReportPayload | null, overrides: Record<string, unknown> = {}) {
  return {
    data: payload === null ? undefined : { data: payload, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

function orgOptions(items: { id: string; name: string }[]) {
  return {
    data: {
      status: 200,
      data: {
        items,
        page: { page: 1, page_size: 100, total_items: items.length, total_pages: 1 },
      },
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

const OVERTIME: OvertimeReport = {
  from: "2026-03-01",
  to: "2026-04-30",
  group_by: ["department"],
  interval: "month",
  rows: [
    {
      bucket: "2026-03-01",
      groups: [{ dimension: "department", key: "d1", label: "Backend" }],
      verified_hours: "3.75",
      verified_request_count: 2,
      verified_day_count: 2,
      active_employee_count: 2,
      hours_per_employee: "1.88",
    },
    {
      bucket: "2026-04-01",
      groups: [{ dimension: "department", key: "d2", label: "Frontend" }],
      verified_hours: "2.50",
      verified_request_count: 1,
      verified_day_count: 1,
      active_employee_count: 1,
      hours_per_employee: "2.50",
    },
  ],
};

const ATTENDANCE: AttendanceReport = {
  from: "2026-03-01",
  to: "2026-04-30",
  group_by: ["department"],
  interval: "month",
  rows: [
    {
      bucket: "2026-03-01",
      groups: [{ dimension: "department", key: "d1", label: "Backend" }],
      checkin_count: 12,
      worked_day_count: 11,
      worked_minutes: 4800,
      late_count: 3,
      late_rate: "0.2500",
      avg_minutes_late: "12.5",
    },
  ],
};

const LEAVE: LeaveReport = {
  from: "2026-03-01",
  to: "2026-04-30",
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

const HEADCOUNT: HeadcountReport = {
  from: "2026-03-01",
  to: "2026-04-30",
  group_by: ["department"],
  interval: "month",
  rows: [
    {
      bucket: "2026-03-01",
      groups: [{ dimension: "department", key: "d1", label: "Backend" }],
      headcount: 8,
      joiners: 1,
      leavers: 0,
    },
  ],
};

const EMPTY_OVERTIME: OvertimeReport = { ...OVERTIME, rows: [] };

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useGetOvertimeReport, queryState(OVERTIME));
  wireQuery(useGetAttendanceReport, queryState(ATTENDANCE));
  wireQuery(useGetLeaveReport, queryState(LEAVE));
  wireQuery(useGetHeadcountReport, queryState(HEADCOUNT));
  wireQuery(useListDepartments, orgOptions([{ id: "d1", name: "Backend" }]));
  wireQuery(useListPositions, orgOptions([{ id: "p1", name: "Engineer" }]));
  wireQuery(useListLocations, orgOptions([{ id: "l1", name: "Berlin Office" }]));
});

describe("ReportsWorkspace", () => {
  it("renders the overtime rows, chart and the exact metric table", async () => {
    renderWithClient(<ReportsWorkspace />);

    expect(await screen.findByTestId("report-panel")).toBeInTheDocument();
    expect(screen.getByText("Backend")).toBeInTheDocument();
    expect(screen.getByText("Frontend")).toBeInTheDocument();
    expect(screen.getByText("3.75")).toBeInTheDocument();
    expect(screen.getAllByText("2.50")).toHaveLength(2);
    expect(screen.getByText("Mar 2026")).toBeInTheDocument();
    expect(screen.getByTestId("reports-table")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Verified hours/ })).toBeInTheDocument();
  });

  it("bounds very large reports and says which rows are hidden", async () => {
    const largeOvertime: OvertimeReport = {
      ...OVERTIME,
      rows: Array.from({ length: 201 }, (_, index) => ({
        bucket: "2026-03-01",
        groups: [
          {
            dimension: "department" as const,
            key: `d-${index}`,
            label: `Department ${index}`,
          },
        ],
        verified_hours: "1.00",
        verified_request_count: 1,
        verified_day_count: 1,
        active_employee_count: 1,
        hours_per_employee: "1.00",
      })),
    };
    wireQuery(useGetOvertimeReport, queryState(largeOvertime));

    renderWithClient(<ReportsWorkspace />);

    expect(await screen.findByTestId("reports-table")).toBeInTheDocument();
    expect(screen.getAllByTestId("report-row")).toHaveLength(200);
    expect(screen.getByText(/Showing the first 200 of 201 rows/)).toBeInTheDocument();
    expect(screen.getByText(/Chart shows the first 12 groups/)).toBeInTheDocument();
  });

  it("shows the stable loading placeholder while the first page loads", () => {
    wireQuery(useGetOvertimeReport, queryState(null, { isPending: true }));
    renderWithClient(<ReportsWorkspace />);

    expect(screen.getByTestId("report-loading")).toBeInTheDocument();
    expect(screen.getByText("Loading report…")).toBeInTheDocument();
  });

  it("shows an invitation when the range has no rows", () => {
    wireQuery(useGetOvertimeReport, queryState(EMPTY_OVERTIME));
    renderWithClient(<ReportsWorkspace />);

    expect(
      screen.getByText(
        "No verified overtime in this range. Claims appear here after HR verifies them.",
      ),
    ).toBeInTheDocument();
  });

  it("maps reports_unavailable to a retryable error state", async () => {
    const refetch = vi.fn();
    wireQuery(
      useGetOvertimeReport,
      queryState(null, {
        isError: true,
        error: apiError(503, "reports_unavailable", "unavailable"),
        refetch,
      }),
    );
    renderWithClient(<ReportsWorkspace />);

    expect(screen.getByText("Report unavailable")).toBeInTheDocument();
    expect(screen.getByText(/analytics store is not answering/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("renders a denied call as an access state without a retry", () => {
    wireQuery(
      useGetOvertimeReport,
      queryState(null, {
        isError: true,
        error: apiError(403, "forbidden", "Not allowed"),
      }),
    );
    renderWithClient(<ReportsWorkspace />);

    expect(screen.getByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("You do not have access to reports.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("switches tabs and queries the newly visible report", async () => {
    renderWithClient(<ReportsWorkspace />);
    await screen.findByTestId("report-panel");

    await userEvent.click(screen.getByRole("tab", { name: "Attendance" }));

    expect(await screen.findByText("80h")).toBeInTheDocument();
    expect(screen.getByText("25.0%")).toBeInTheDocument();
    const calls = vi.mocked(useGetAttendanceReport).mock.calls;
    const [params, options] = calls[calls.length - 1] ?? [];
    expect(params?.group_by).toBe("department");
    expect(options?.query?.enabled).toBe(true);
  });

  it("re-queries with the new date when a filter changes", async () => {
    renderWithClient(<ReportsWorkspace />);
    await screen.findByTestId("report-panel");

    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-04-01" },
    });

    const calls = vi.mocked(useGetOvertimeReport).mock.calls;
    const [params] = calls[calls.length - 1] ?? [];
    expect(params?.from).toBe("2026-04-01");
    expect(params?.to).toBeTruthy();
  });

  it("keeps only leave dimensions on the leave tab", async () => {
    renderWithClient(<ReportsWorkspace />);
    await screen.findByTestId("report-panel");

    await userEvent.click(screen.getByRole("tab", { name: "Leave" }));

    expect(await screen.findByText("Approved days")).toBeInTheDocument();
    const calls = vi.mocked(useGetLeaveReport).mock.calls;
    const [params] = calls[calls.length - 1] ?? [];
    expect(params?.group_by).toBe("department");
  });

  it("offers leave_type as a grouping dimension on the leave tab only", async () => {
    renderWithClient(<ReportsWorkspace />);
    await screen.findByTestId("report-panel");
    await userEvent.click(screen.getByRole("tab", { name: "Leave" }));

    await userEvent.click(screen.getByRole("button", { name: "Group by dimensions" }));
    expect(await screen.findByRole("menuitemcheckbox", { name: "Leave type" })).toBeInTheDocument();
  });
});
