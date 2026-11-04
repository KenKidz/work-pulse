/**
 * The dashboard's self-service rail: open-record state, balances, payslip and
 * the no-employee fallback. Queries are mocked at the generated boundary; the
 * card's shape and copy are the thing under test.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useListAttendanceRecords,
  useListLeaveBalances,
  useListLeaveTypes,
  useListMyPayslips,
} from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { MyDayCard } from "@/features/dashboard/components/MyDayCard";
import { dayjs } from "@/lib/datetime";

vi.mock("@workpulse/api-client", () => ({
  useListAttendanceRecords: vi.fn(),
  useListLeaveBalances: vi.fn(),
  useListLeaveTypes: vi.fn(),
  useListMyPayslips: vi.fn(),
}));

const TENANT = "01900000-0000-7000-8000-000000000001";
const TODAY = dayjs().format("YYYY-MM-DD");
const EMPLOYEE_ID = "01900000-0000-7000-8000-00000000a001";

const USER: User = {
  id: "01900000-0000-7000-8000-0000000000b1",
  tenant_id: TENANT,
  email: "manager@workpulse.test",
  role: "manager",
  employee_id: EMPLOYEE_ID,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const NO_LINK_USER: User = { ...USER, employee_id: null };

const OPEN_RECORD = {
  id: "record-1",
  tenant_id: TENANT,
  employee_id: EMPLOYEE_ID,
  workday: TODAY,
  checked_in_at: "2026-11-04T01:57:00Z",
  checked_out_at: null,
  work_mode: "onsite",
  late: false,
  shift_id: null,
  check_in_note: null,
  check_out_note: null,
  duration_minutes: null,
  created_at: "2026-11-04T01:57:00Z",
};

const TYPES = [
  { id: "type-annual", code: "annual", name: "Annual leave", counts_against_balance: true },
  { id: "type-sick", code: "sick", name: "Sick leave", counts_against_balance: true },
];

const BALANCES = [
  {
    id: "balance-1",
    tenant_id: TENANT,
    employee_id: EMPLOYEE_ID,
    leave_type_id: "type-annual",
    year: 2026,
    entitlement_days: 20,
    used_days: 8,
    remaining_days: 12,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "balance-2",
    tenant_id: TENANT,
    employee_id: EMPLOYEE_ID,
    leave_type_id: "type-sick",
    year: 2026,
    entitlement_days: 10,
    used_days: 5,
    remaining_days: 5,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

const PAYSLIP = {
  id: "payslip-1",
  tenant_id: TENANT,
  employee_id: EMPLOYEE_ID,
  period: "2026-10",
  status: "issued",
  net: 4800,
  issued_at: "2026-11-03T00:00:00Z",
  created_at: "2026-11-03T00:00:00Z",
  updated_at: "2026-11-03T00:00:00Z",
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

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useListAttendanceRecords).mockReturnValue(
    queryState({
      status: 200,
      data: {
        items: [OPEN_RECORD],
        page: { page: 1, page_size: 5, total_items: 1, total_pages: 1 },
      },
    }) as never,
  );
  vi.mocked(useListLeaveBalances).mockReturnValue(
    queryState({ status: 200, data: BALANCES }) as never,
  );
  vi.mocked(useListLeaveTypes).mockReturnValue(queryState({ status: 200, data: TYPES }) as never);
  vi.mocked(useListMyPayslips).mockReturnValue(
    queryState({
      status: 200,
      data: {
        items: [PAYSLIP],
        page: { page: 1, page_size: 1, total_items: 1, total_pages: 1 },
      },
    }) as never,
  );
});

describe("MyDayCard", () => {
  it("shows the open record, balances and latest payslip", () => {
    render(<MyDayCard user={USER} />);

    expect(screen.getByText(/Checked in at/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open attendance" })).toHaveAttribute(
      "href",
      "/attendance",
    );
    expect(screen.getByText("12 of 20 days")).toBeInTheDocument();
    expect(screen.getByText("5 of 10 days")).toBeInTheDocument();
    expect(screen.getByText("October 2026")).toBeInTheDocument();
    expect(screen.getByText("Net 4,800.00")).toBeInTheDocument();
    expect(screen.getByText("Issued")).toBeInTheDocument();

    expect(vi.mocked(useListAttendanceRecords).mock.calls[0]?.[0]).toMatchObject({
      employee_id: EMPLOYEE_ID,
      from: dayjs(TODAY).subtract(7, "day").format("YYYY-MM-DD"),
      to: dayjs(TODAY).add(1, "day").format("YYYY-MM-DD"),
    });
  });

  it("shows the check-in invitation when there is no open record", () => {
    vi.mocked(useListAttendanceRecords).mockReturnValue(
      queryState({
        status: 200,
        data: {
          items: [],
          page: { page: 1, page_size: 5, total_items: 0, total_pages: 1 },
        },
      }) as never,
    );

    render(<MyDayCard user={USER} />);

    expect(screen.getByText("Not checked in")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Check in" })).toHaveAttribute("href", "/attendance");
  });

  it("falls back to quick actions when the account has no employee link", () => {
    render(<MyDayCard user={NO_LINK_USER} />);

    expect(screen.getByTestId("quick-actions")).toBeInTheDocument();
    expect(screen.queryByTestId("my-day")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Command center" })).toHaveAttribute(
      "href",
      "/command-center",
    );
  });
});
