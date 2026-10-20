/**
 * TeamApprovals — the approval inbox: pending rows with joined names, own
 * requests withheld from decision, approve/reject with an optional note, the
 * server's self-approval 403 rendered as a visible error state, and the role
 * gate. The generated api-client is mocked; hooks and components are real.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useApproveLeaveRequest,
  useGetCurrentEmployee,
  useListEmployees,
  useListLeaveRequests,
  useRejectLeaveRequest,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { TeamApprovals } from "@/features/leave/components/TeamApprovals";

import {
  apiError,
  itemState,
  requestPageState,
  renderWithClient,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "./helpers";

const TENANT_ID = "01900000-0000-7000-8000-000000000001";
const MANAGER_ID = "01900000-0000-7000-8000-000000000023";
const WENDY_ID = "01900000-0000-7000-8000-000000000021";

const auth = vi.hoisted(() => ({
  user: {
    id: "01900000-0000-7000-8000-000000000014",
    tenant_id: "01900000-0000-7000-8000-000000000001",
    email: "manager@workpulse.test",
    role: "manager",
    employee_id: "01900000-0000-7000-8000-000000000023",
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

const MANAGER = {
  id: MANAGER_ID,
  tenant_id: TENANT_ID,
  employee_code: "EMP-DEV02",
  first_name: "Morgan",
  last_name: "Manager",
  email: "manager@workpulse.test",
  phone: null,
  department_id: "01900000-0000-7000-8000-0000000d0003",
  position_id: "01900000-0000-7000-8000-0000000e0003",
  location_id: "01900000-0000-7000-8000-0000000c0001",
  manager_id: null,
  employment_type: "full_time" as const,
  status: "active" as const,
  hired_at: "2026-01-05",
  terminated_at: null,
  created_at: "2026-01-05T00:00:00Z",
  updated_at: "2026-01-05T00:00:00Z",
};

const WENDY = {
  id: WENDY_ID,
  employee_code: "EMP-DEV01",
  first_name: "Wendy",
  last_name: "Worker",
  email: "worker@workpulse.test",
  department_id: "01900000-0000-7000-8000-0000000d0003",
  position_id: "01900000-0000-7000-8000-0000000e0001",
  location_id: "01900000-0000-7000-8000-0000000c0001",
  manager_id: MANAGER_ID,
  employment_type: "full_time" as const,
  status: "active" as const,
  hired_at: "2026-01-05",
  terminated_at: null,
};

function leaveRequest(overrides: Record<string, unknown>) {
  return {
    id: "",
    employee_id: "",
    tenant_id: TENANT_ID,
    leave_type_id: "01900000-0000-7000-8000-00000000t001",
    leave_type_code: "annual",
    start_date: "2030-11-02",
    end_date: "2030-11-04",
    days: 3,
    status: "pending",
    reason: "Family visit",
    decision_note: null,
    decided_by: null,
    decided_at: null,
    created_at: "2026-10-20T03:00:00Z",
    updated_at: "2026-10-20T03:00:00Z",
    ...overrides,
  };
}

const WENDY_REQUEST = leaveRequest({
  id: "01900000-0000-7000-8000-00000000r001",
  employee_id: WENDY_ID,
});

const OWN_REQUEST = leaveRequest({
  id: "01900000-0000-7000-8000-00000000r002",
  employee_id: MANAGER_ID,
  reason: null,
});

const approve = vi.fn<(variables: unknown) => Promise<unknown>>();
const reject = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = {
    ...auth.user,
    role: "manager",
    employee_id: MANAGER_ID,
  };
  wireQuery(useListLeaveRequests, requestPageState([WENDY_REQUEST, OWN_REQUEST]));
  wireQuery(useListEmployees, requestPageState([WENDY]));
  wireQuery(useGetCurrentEmployee, itemState(MANAGER));
  wireMutation(useApproveLeaveRequest as unknown as MutateFn, approve);
  wireMutation(useRejectLeaveRequest as unknown as MutateFn, reject);
});

describe("TeamApprovals", () => {
  it("lists pending requests, withholds actions on the actor's own request", async () => {
    renderWithClient(<TeamApprovals />);

    expect(await screen.findByText("Wendy Worker")).toBeInTheDocument();
    expect(screen.getByText("Morgan Manager")).toBeInTheDocument();
    expect(screen.getByText("Family visit")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve leave request for Wendy Worker" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reject leave request for Wendy Worker" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /for Morgan Manager/ })).not.toBeInTheDocument();
    expect(screen.getByText("Your own request — someone else must decide.")).toBeInTheDocument();
  });

  it("approves with an optional decision note", async () => {
    const user = userEvent.setup();
    approve.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<TeamApprovals />);

    await user.click(
      await screen.findByRole("button", {
        name: "Approve leave request for Wendy Worker",
      }),
    );
    expect(screen.getByRole("heading", { name: "Approve leave request" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Decision note (optional)"), "Enjoy the break.");
    await user.click(screen.getByRole("button", { name: "Approve request" }));

    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith({
        id: WENDY_REQUEST.id,
        data: { decision_note: "Enjoy the break." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Request approved");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("rejects without a note", async () => {
    const user = userEvent.setup();
    reject.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<TeamApprovals />);

    await user.click(
      await screen.findByRole("button", {
        name: "Reject leave request for Wendy Worker",
      }),
    );
    expect(screen.getByRole("heading", { name: "Reject leave request" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reject request" }));

    await waitFor(() =>
      expect(reject).toHaveBeenCalledWith({
        id: WENDY_REQUEST.id,
        data: { decision_note: null },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Request rejected");
  });

  it("renders the server's self-approval refusal as a visible error state", async () => {
    const user = userEvent.setup();
    approve.mockRejectedValue(
      apiError(403, "forbidden", "A leave request cannot be decided by its requester"),
    );
    renderWithClient(<TeamApprovals />);

    await user.click(
      await screen.findByRole("button", {
        name: "Approve leave request for Wendy Worker",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Approve request" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You can’t decide your own leave request.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("hides the inbox from roles that cannot decide", () => {
    auth.user = { ...auth.user, role: "employee", employee_id: WENDY_ID };
    renderWithClient(<TeamApprovals />);

    expect(screen.queryByText("Team approvals")).not.toBeInTheDocument();
  });
});
