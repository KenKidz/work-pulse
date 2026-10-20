/**
 * MyRequestsTable — row rendering (status/type/reason/decision note), the
 * withdraw action, and the `409 leave_not_pending` failure as a visible alert.
 * The generated api-client is mocked; hooks and components are the real thing.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useListLeaveRequests,
  useListLeaveTypes,
  useWithdrawLeaveRequest,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { MyRequestsTable } from "@/features/leave/components/MyRequestsTable";

import {
  apiError,
  listState,
  requestPageState,
  renderWithClient,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "./helpers";

const EMPLOYEE_ID = "01900000-0000-7000-8000-000000000021";

const auth = vi.hoisted(() => ({
  user: {
    id: "01900000-0000-7000-8000-000000000013",
    tenant_id: "01900000-0000-7000-8000-000000000001",
    email: "worker@workpulse.test",
    role: "employee",
    employee_id: "01900000-0000-7000-8000-000000000021" as string | null,
    active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => auth,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
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

const PENDING = {
  id: "01900000-0000-7000-8000-00000000r001",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  employee_id: EMPLOYEE_ID,
  leave_type_id: "01900000-0000-7000-8000-00000000t003",
  leave_type_code: "unpaid" as const,
  start_date: "2030-11-02",
  end_date: "2030-11-04",
  days: 3,
  status: "pending" as const,
  reason: "Family visit",
  decision_note: null,
  decided_by: null,
  decided_at: null,
  created_at: "2026-10-20T03:00:00Z",
  updated_at: "2026-10-20T03:00:00Z",
};

const APPROVED = {
  ...PENDING,
  id: "01900000-0000-7000-8000-00000000r002",
  leave_type_code: "annual" as const,
  start_date: "2030-12-01",
  end_date: "2030-12-02",
  days: 2,
  status: "approved" as const,
  reason: null,
  decision_note: "Approved — enjoy.",
};

const withdraw = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { ...auth.user, role: "employee", employee_id: EMPLOYEE_ID };
  wireQuery(useListLeaveRequests, requestPageState([PENDING, APPROVED]));
  wireQuery(
    useListLeaveTypes,
    listState([
      {
        id: PENDING.leave_type_id,
        code: "unpaid",
        name: "Unpaid leave",
        counts_against_balance: false,
      },
    ]),
  );
  wireMutation(useWithdrawLeaveRequest as unknown as MutateFn, withdraw);
});

describe("MyRequestsTable", () => {
  it("renders own requests with type, status, reason and decision note", async () => {
    renderWithClient(<MyRequestsTable />);

    expect(await screen.findByText("Unpaid leave")).toBeInTheDocument();
    expect(screen.getByText("Annual leave")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getByText("Family visit")).toBeInTheDocument();
    expect(screen.getByText("Approved — enjoy.")).toBeInTheDocument();
  });

  it("withdraws a pending request and toasts the outcome", async () => {
    const user = userEvent.setup();
    withdraw.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<MyRequestsTable />);

    await user.click(
      await screen.findByRole("button", {
        name: "Withdraw leave request starting 2030-11-02",
      }),
    );

    await waitFor(() => expect(withdraw).toHaveBeenCalledWith({ id: PENDING.id }));
    expect(toast.success).toHaveBeenCalledWith("Request withdrawn");
  });

  it("renders a 409 withdraw failure as a visible alert", async () => {
    const user = userEvent.setup();
    withdraw.mockRejectedValue(apiError(409, "leave_not_pending", "Leave request is not pending"));
    renderWithClient(<MyRequestsTable />);

    await user.click(
      await screen.findByRole("button", {
        name: "Withdraw leave request starting 2030-11-02",
      }),
    );

    expect(
      await screen.findByText("This request is no longer pending. Refresh the list and try again."),
    ).toBeInTheDocument();
  });

  it("explains the missing employee link instead of offering the form", () => {
    auth.user = { ...auth.user, employee_id: null };
    renderWithClient(<MyRequestsTable />);

    expect(
      screen.getByText(
        "Your account isn’t linked to an employee record, so you can’t request leave.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request leave" })).not.toBeInTheDocument();
  });
});
