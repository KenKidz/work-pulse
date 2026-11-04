/**
 * Dashboard composition tests — the dashboard's queries are mocked at the
 * feature-hook boundary so the test proves the role-aware layout, the joined
 * directory names, the deep links and the empty/error states. The self-service
 * card is stubbed here and covered by its own suite.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useGetCurrentEmployee } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { useLeaveRequests } from "@/features/leave/hooks/use-leave";
import { useOvertimeRequests } from "@/features/overtime/hooks/use-overtime";
import { useEmployeeDirectory } from "@/features/employees/hooks/use-employee-directory";
import { useRealtimeBoard } from "@/features/realtime/hooks/use-realtime-board";
import { OperationsDashboard } from "@/features/dashboard/components/OperationsDashboard";

import { dayjs } from "@/lib/datetime";

vi.mock("@/features/leave/hooks/use-leave", () => ({
  useLeaveRequests: vi.fn(),
  isForbiddenError: vi.fn(() => false),
}));

vi.mock("@/features/overtime/hooks/use-overtime", () => ({
  useOvertimeRequests: vi.fn(),
}));

vi.mock("@/features/overtime/labels", () => ({
  isForbiddenError: vi.fn(() => false),
  OVERTIME_HR_STEP_ROLES: ["hr", "admin", "super_admin"],
}));

vi.mock("@/features/realtime/hooks/use-realtime-board", () => ({
  useRealtimeBoard: vi.fn(),
  isForbiddenError: vi.fn(() => false),
}));

vi.mock("@/features/employees/hooks/use-employee-directory", () => ({
  useEmployeeDirectory: vi.fn(),
}));

vi.mock("@/features/dashboard/components/MyDayCard", () => ({
  MyDayCard: () => <div data-testid="my-day-stub" />,
}));

vi.mock("@workpulse/api-client", () => ({
  useGetCurrentEmployee: vi.fn(),
}));

const TENANT = "01900000-0000-7000-8000-000000000001";
const TODAY = dayjs().format("YYYY-MM-DD");
const ME_ID = "01900000-0000-7000-8000-00000000a001";
const WENDY_ID = "01900000-0000-7000-8000-00000000a002";

const ME = {
  id: ME_ID,
  tenant_id: TENANT,
  employee_code: "EMP-00001",
  first_name: "Morgan",
  last_name: "Manager",
  email: "manager@workpulse.test",
  department_id: null,
  position_id: null,
  location_id: null,
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2026-01-05",
  terminated_at: null,
  created_at: "2026-01-05T00:00:00Z",
  updated_at: "2026-01-05T00:00:00Z",
};

const WENDY = {
  ...ME,
  id: WENDY_ID,
  employee_code: "EMP-00002",
  first_name: "Wendy",
  last_name: "Worker",
  email: "worker@workpulse.test",
  manager_id: ME_ID,
};

const USER: User = {
  id: "01900000-0000-7000-8000-0000000000b1",
  tenant_id: TENANT,
  email: "manager@workpulse.test",
  role: "manager",
  employee_id: ME_ID,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const BOARD = {
  date: TODAY,
  counts: { present: 1, remote: 1, late: 1, absent: 2, on_leave: 0, checked_out: 0 },
  presence: [
    {
      employee_id: ME_ID,
      status: "present",
      late: false,
      minutes_late: null,
      since: "2026-11-04T01:57:00Z",
    },
    {
      employee_id: WENDY_ID,
      status: "remote",
      late: false,
      minutes_late: null,
      since: "2026-11-04T02:04:00Z",
    },
    {
      employee_id: "01900000-0000-7000-8000-00000000a003",
      status: "absent",
      late: false,
      minutes_late: null,
      since: null,
    },
  ],
  activity: [
    { occurred_at: "2026-11-04T02:04:00Z", employee_id: WENDY_ID, action: "checked_in" },
    { occurred_at: "2026-11-04T01:57:00Z", employee_id: ME_ID, action: "checked_in" },
  ],
};

const LEAVE = {
  id: "leave-1",
  employee_id: WENDY_ID,
  leave_type_id: "type-annual",
  leave_type_code: "annual",
  start_date: "2026-11-12",
  end_date: "2026-11-14",
  days: 3,
  status: "pending",
  reason: "Family trip",
  created_at: "2026-11-04T01:00:00Z",
};

const OVERTIME = {
  id: "ot-1",
  employee_id: WENDY_ID,
  work_date: "2026-11-03",
  hours: 3.5,
  status: "requested",
  reason: "Release support",
  created_at: "2026-11-04T00:30:00Z",
};

function queryState(data: unknown) {
  return {
    data,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

function page(items: unknown[]) {
  return {
    items,
    page: { page: 1, page_size: 4, total_items: items.length, total_pages: 1 },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useRealtimeBoard).mockReturnValue(queryState({ status: 200, data: BOARD }) as never);
  vi.mocked(useEmployeeDirectory).mockReturnValue(queryState([WENDY]) as never);
  vi.mocked(useLeaveRequests).mockReturnValue(
    queryState({ status: 200, data: page([LEAVE]) }) as never,
  );
  vi.mocked(useOvertimeRequests).mockReturnValue(
    queryState({ status: 200, data: page([OVERTIME]) }) as never,
  );
  vi.mocked(useGetCurrentEmployee).mockReturnValue(queryState({ status: 200, data: ME }) as never);
});

describe("OperationsDashboard", () => {
  it("renders counts, approvals, activity and presence with joined names", async () => {
    render(<OperationsDashboard user={USER} />);

    expect(screen.getByTestId("count-present")).toHaveTextContent("1");
    expect(screen.getByTestId("count-remote")).toHaveTextContent("1");
    expect(screen.getByTestId("count-absent")).toHaveTextContent("2");

    expect(screen.getByTestId("my-day-stub")).toBeInTheDocument();
    const attention = screen.getByTestId("needs-attention");
    expect(attention).toBeInTheDocument();
    expect(within(attention).getByText("Wendy Worker")).toBeInTheDocument();
    expect(within(attention).getByText(/Annual leave/)).toBeInTheDocument();

    expect(screen.getByText("Recent activity")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-presence")).toBeInTheDocument();
    expect(screen.getByText("Showing 3 of 3 people")).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Request leave" })).toHaveAttribute("href", "/leave");
    expect(screen.getByRole("link", { name: "Open attendance" })).toHaveAttribute(
      "href",
      "/attendance",
    );
  });

  it("switches to overtime and links the queue", async () => {
    const user = userEvent.setup();
    render(<OperationsDashboard user={USER} />);

    await user.click(screen.getByRole("tab", { name: /Overtime/ }));

    expect(screen.getByText(/3.5 h/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open overtime queue" })).toHaveAttribute(
      "href",
      "/overtime",
    );
  });

  it("shows the empty state when both queues are clear", async () => {
    vi.mocked(useLeaveRequests).mockReturnValue(
      queryState({ status: 200, data: page([]) }) as never,
    );
    vi.mocked(useOvertimeRequests).mockReturnValue(
      queryState({ status: 200, data: page([]) }) as never,
    );

    const user = userEvent.setup();
    render(<OperationsDashboard user={USER} />);

    expect(screen.getByText("No pending leave requests in your scope.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Overtime/ }));
    expect(screen.getByText("No overtime claims waiting for you.")).toBeInTheDocument();
  });

  it("shows the HR verification step for HR and admins", () => {
    render(<OperationsDashboard user={{ ...USER, role: "hr" }} />);

    expect(useOvertimeRequests).toHaveBeenCalledWith(
      expect.objectContaining({ status: "manager_approved" }),
    );
  });

  it("keeps the decision queue usable when the snapshot fails", () => {
    vi.mocked(useRealtimeBoard).mockReturnValue({
      ...queryState(undefined),
      isError: true,
      error: new Error("board down"),
    } as never);

    render(<OperationsDashboard user={USER} />);

    expect(screen.getByText("Could not load today's numbers")).toBeInTheDocument();
    expect(screen.getByTestId("needs-attention")).toBeInTheDocument();
    expect(screen.getByTestId("my-day-stub")).toBeInTheDocument();
  });
});
