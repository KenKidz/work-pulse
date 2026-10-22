/**
 * MyOvertimeTable — own claims with status/hours/notes, withdraw on the
 * pre-verification states only, the status/date filters, the `409
 * overtime_not_open` failure as a visible alert, and the missing employee link
 * (leave MyRequestsTable pattern). The generated api-client is mocked; hooks
 * and components are the real thing.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useListOvertimeRequests, useWithdrawOvertimeRequest } from "@workpulse/api-client";
import { toast } from "sonner";

import { MyOvertimeTable } from "@/features/overtime/components/MyOvertimeTable";
import type { ListOvertimeRequestsParams, OvertimeRequest } from "@/features/overtime/types";

import {
  apiError,
  requestPageState,
  renderWithClient,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

const TENANT_ID = "01900000-0000-7000-8000-000000000001";
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
  getListOvertimeRequestsQueryKey: () => ["/api/v1/overtime/requests"],
  useListOvertimeRequests: vi.fn(),
  useListEmployees: vi.fn(),
  useGetCurrentEmployee: vi.fn(),
  useCreateOvertimeRequest: vi.fn(),
  useApproveOvertimeRequest: vi.fn(),
  useVerifyOvertimeRequest: vi.fn(),
  useRejectOvertimeRequest: vi.fn(),
  useWithdrawOvertimeRequest: vi.fn(),
}));

function overtimeRequest(overrides: Partial<OvertimeRequest>): OvertimeRequest {
  return {
    id: "01900000-0000-7000-8000-00000000o001",
    tenant_id: TENANT_ID,
    employee_id: EMPLOYEE_ID,
    work_date: "2030-11-02",
    hours: 2.5,
    status: "requested",
    reason: null,
    manager_note: null,
    hr_note: null,
    approved_by: null,
    approved_at: null,
    verified_by: null,
    verified_at: null,
    rejected_by: null,
    rejected_at: null,
    rejected_from: null,
    withdrawn_at: null,
    included_in_run_id: null,
    created_at: "2026-10-20T03:00:00Z",
    updated_at: "2026-10-20T03:00:00Z",
    ...overrides,
  };
}

const REQUESTED = overtimeRequest({
  work_date: "2030-11-02",
  hours: 2.5,
  status: "requested",
  reason: "Quarter close",
});

const MANAGER_APPROVED = overtimeRequest({
  id: "01900000-0000-7000-8000-00000000o002",
  work_date: "2030-11-03",
  hours: 3,
  status: "manager_approved",
  manager_note: "Approved — busy week.",
});

const HR_VERIFIED = overtimeRequest({
  id: "01900000-0000-7000-8000-00000000o003",
  work_date: "2030-11-04",
  hours: 4.25,
  status: "hr_verified",
  manager_note: "Agreed.",
  hr_note: "Confirmed against the shift log.",
});

const withdraw = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { ...auth.user, role: "employee", employee_id: EMPLOYEE_ID };
  wireQuery(useListOvertimeRequests, requestPageState([REQUESTED, MANAGER_APPROVED, HR_VERIFIED]));
  wireMutation(useWithdrawOvertimeRequest as unknown as MutateFn, withdraw);
});

describe("MyOvertimeTable", () => {
  it("renders own claims with work date, hours, status and decision notes", async () => {
    renderWithClient(<MyOvertimeTable />);

    expect(await screen.findByTestId("my-overtime")).toBeInTheDocument();
    expect(screen.getByText("Nov 2, 2030")).toBeInTheDocument();
    expect(screen.getByText("Nov 3, 2030")).toBeInTheDocument();
    expect(screen.getByText("Nov 4, 2030")).toBeInTheDocument();
    expect(screen.getByText("2.5")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("4.25")).toBeInTheDocument();
    expect(screen.getByText("Requested")).toBeInTheDocument();
    expect(screen.getByText("Manager approved")).toBeInTheDocument();
    expect(screen.getByText("HR verified")).toBeInTheDocument();
    expect(screen.getByText("Quarter close")).toBeInTheDocument();
    expect(screen.getByText("Approved — busy week.")).toBeInTheDocument();
    expect(screen.getByText("Confirmed against the shift log.")).toBeInTheDocument();
  });

  it("withdraws an open claim and hides the action once HR verified it", async () => {
    const user = userEvent.setup();
    withdraw.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<MyOvertimeTable />);

    expect(
      await screen.findByRole("button", {
        name: "Withdraw overtime request on 2030-11-02",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Withdraw overtime request on 2030-11-03",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Withdraw overtime request on 2030-11-04",
      }),
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: "Withdraw overtime request on 2030-11-02",
      }),
    );

    await waitFor(() => expect(withdraw).toHaveBeenCalledWith({ id: REQUESTED.id }));
    expect(toast.success).toHaveBeenCalledWith("Overtime request withdrawn");
  });

  it("renders a 409 overtime_not_open failure as a visible alert", async () => {
    const user = userEvent.setup();
    withdraw.mockRejectedValue(
      apiError(409, "overtime_not_open", "Overtime request cannot be withdrawn"),
    );
    renderWithClient(<MyOvertimeTable />);

    await user.click(
      await screen.findByRole("button", {
        name: "Withdraw overtime request on 2030-11-02",
      }),
    );

    expect(
      await screen.findByText(
        "This request can no longer be withdrawn or rejected. Refresh and try again.",
      ),
    ).toBeInTheDocument();
  });

  it("filters by status through the pinned lowercase wire value", async () => {
    const user = userEvent.setup();
    renderWithClient(<MyOvertimeTable />);

    await user.click(await screen.findByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Manager approved" }));

    await waitFor(() => {
      const calls = vi.mocked(useListOvertimeRequests).mock.calls;
      const lastParams = calls[calls.length - 1]?.[0] as ListOvertimeRequestsParams | undefined;
      expect(lastParams?.status).toBe("manager_approved");
    });
  });

  it("explains the missing employee link instead of offering the form", () => {
    auth.user = { ...auth.user, employee_id: null };
    renderWithClient(<MyOvertimeTable />);

    expect(
      screen.getByText(
        "Your account isn’t linked to an employee record, so you can’t claim overtime.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request overtime" })).not.toBeInTheDocument();
  });
});
