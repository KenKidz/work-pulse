/**
 * LeaveBalanceCards — one cell per (type, year) with remaining/used/
 * entitlement, plus the empty state before the first request creates a
 * balance. The generated api-client is mocked; hooks and component are real.
 */

import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useListLeaveBalances, useListLeaveTypes } from "@workpulse/api-client";

import { LeaveBalanceCards } from "@/features/leave/components/LeaveBalanceCards";

import { listState, renderWithClient, wireQuery } from "./helpers";

const EMPLOYEE_ID = "01900000-0000-7000-8000-000000000021";
const TYPE_ID = "01900000-0000-7000-8000-00000000t001";

const auth = vi.hoisted(() => ({
  user: {
    id: "01900000-0000-7000-8000-000000000013",
    tenant_id: "01900000-0000-7000-8000-000000000001",
    email: "worker@workpulse.test",
    role: "employee",
    employee_id: "01900000-0000-7000-8000-000000000021",
    active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => auth,
}));

vi.mock("@workpulse/api-client", () => ({
  getListLeaveRequestsQueryKey: () => ["/api/v1/leave/requests"],
  getListLeaveBalancesQueryKey: () => ["/api/v1/leave/balances"],
  getListLeaveTypesQueryKey: () => ["/api/v1/leave/types"],
  useListLeaveRequests: vi.fn(),
  useListLeaveBalances: vi.fn(),
  useListLeaveTypes: vi.fn(),
  useGetCurrentEmployee: vi.fn(),
  useListEmployees: vi.fn(),
  useCreateLeaveRequest: vi.fn(),
  useApproveLeaveRequest: vi.fn(),
  useRejectLeaveRequest: vi.fn(),
  useWithdrawLeaveRequest: vi.fn(),
}));

const BALANCE = {
  tenant_id: "01900000-0000-7000-8000-000000000001",
  employee_id: EMPLOYEE_ID,
  leave_type_id: TYPE_ID,
  year: 2026,
  entitlement_days: 12,
  used_days: 3,
  remaining_days: 9,
};

const TYPE = {
  id: TYPE_ID,
  code: "annual" as const,
  name: "Annual leave",
  counts_against_balance: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useListLeaveBalances, listState([BALANCE]));
  wireQuery(useListLeaveTypes, listState([TYPE]));
});

describe("LeaveBalanceCards", () => {
  it("renders the remaining, used and entitled days per type and year", () => {
    renderWithClient(<LeaveBalanceCards />);

    expect(screen.getByText(/Annual leave/)).toBeInTheDocument();
    expect(screen.getByText("2026")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
    expect(screen.getByText("3 used of 12 days")).toBeInTheDocument();
  });

  it("invites the first request when no balance exists yet", () => {
    wireQuery(useListLeaveBalances, listState([]));
    renderWithClient(<LeaveBalanceCards />);

    expect(
      screen.getByText(
        "No balances yet — annual and sick balances appear with your first request for that year.",
      ),
    ).toBeInTheDocument();
  });
});
