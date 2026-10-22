/**
 * ManagerInbox — the manager-step queue: team claims with joined names, own
 * claims withheld from decision, approve/reject with an optional note, the
 * server's self-decision 403 rendered in place, and the role gate (leave
 * TeamApprovals pattern). The generated api-client is mocked.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useApproveOvertimeRequest,
  useGetCurrentEmployee,
  useListEmployees,
  useListOvertimeRequests,
  useRejectOvertimeRequest,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { ManagerInbox } from "@/features/overtime/components/ManagerInbox";
import type { OvertimeRequest } from "@/features/overtime/types";

import {
  apiError,
  itemState,
  requestPageState,
  renderWithClient,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

const TENANT_ID = "01900000-0000-7000-8000-000000000001";
const MANAGER_ID = "01900000-0000-7000-8000-000000000023";
const WENDY_ID = "01900000-0000-7000-8000-000000000021";

const auth = vi.hoisted(() => ({
  user: {
    id: "01900000-0000-7000-8000-000000000014",
    tenant_id: "01900000-0000-7000-8000-000000000001",
    email: "manager@workpulse.test",
    role: "manager",
    employee_id: "01900000-0000-7000-8000-000000000023" as string | null,
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
  tenant_id: TENANT_ID,
  employee_code: "EMP-DEV01",
  first_name: "Wendy",
  last_name: "Worker",
  email: "worker@workpulse.test",
  phone: null,
  department_id: "01900000-0000-7000-8000-0000000d0003",
  position_id: "01900000-0000-7000-8000-0000000e0001",
  location_id: "01900000-0000-7000-8000-0000000c0001",
  manager_id: MANAGER_ID,
  employment_type: "full_time" as const,
  status: "active" as const,
  hired_at: "2026-01-05",
  terminated_at: null,
  created_at: "2026-01-05T00:00:00Z",
  updated_at: "2026-01-05T00:00:00Z",
};

function overtimeRequest(overrides: Partial<OvertimeRequest>): OvertimeRequest {
  return {
    id: "",
    tenant_id: TENANT_ID,
    employee_id: WENDY_ID,
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

const WENDY_REQUEST = overtimeRequest({
  id: "01900000-0000-7000-8000-00000000o001",
  employee_id: WENDY_ID,
  work_date: "2030-11-02",
  hours: 2.5,
  reason: "Quarter close",
});

const OWN_REQUEST = overtimeRequest({
  id: "01900000-0000-7000-8000-00000000o002",
  employee_id: MANAGER_ID,
  work_date: "2030-11-03",
  hours: 1.5,
});

const approve = vi.fn<(variables: unknown) => Promise<unknown>>();
const reject = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { ...auth.user, role: "manager", employee_id: MANAGER_ID };
  wireQuery(useListOvertimeRequests, requestPageState([WENDY_REQUEST, OWN_REQUEST]));
  wireQuery(useListEmployees, requestPageState([WENDY]));
  wireQuery(useGetCurrentEmployee, itemState(MANAGER));
  wireMutation(useApproveOvertimeRequest as unknown as MutateFn, approve);
  wireMutation(useRejectOvertimeRequest as unknown as MutateFn, reject);
});

describe("ManagerInbox", () => {
  it("lists requested claims and withholds actions on the actor's own claim", async () => {
    renderWithClient(<ManagerInbox />);

    expect(await screen.findByText("Wendy Worker")).toBeInTheDocument();
    expect(screen.getByText("Morgan Manager")).toBeInTheDocument();
    expect(screen.getByText("Quarter close")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve overtime request for Wendy Worker" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reject overtime request for Wendy Worker" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /for Morgan Manager/ })).not.toBeInTheDocument();
    expect(screen.getByText("Your own request — someone else must decide.")).toBeInTheDocument();
  });

  it("approves with an optional decision note", async () => {
    const user = userEvent.setup();
    approve.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<ManagerInbox />);

    await user.click(
      await screen.findByRole("button", {
        name: "Approve overtime request for Wendy Worker",
      }),
    );
    expect(screen.getByRole("heading", { name: "Approve overtime request" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Decision note (optional)"), "Approved — busy week.");
    await user.click(screen.getByRole("button", { name: "Approve request" }));

    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith({
        id: WENDY_REQUEST.id,
        data: { note: "Approved — busy week." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Overtime approved");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("rejects without a note", async () => {
    const user = userEvent.setup();
    reject.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<ManagerInbox />);

    await user.click(
      await screen.findByRole("button", {
        name: "Reject overtime request for Wendy Worker",
      }),
    );
    expect(screen.getByRole("heading", { name: "Reject overtime request" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reject request" }));

    await waitFor(() =>
      expect(reject).toHaveBeenCalledWith({
        id: WENDY_REQUEST.id,
        data: { note: null },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Overtime rejected");
  });

  it("renders the server's self-decision refusal as a visible error state", async () => {
    const user = userEvent.setup();
    approve.mockRejectedValue(
      apiError(403, "forbidden", "An overtime request cannot be decided by its requester"),
    );
    renderWithClient(<ManagerInbox />);

    await user.click(
      await screen.findByRole("button", {
        name: "Approve overtime request for Wendy Worker",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Approve request" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You can’t decide your own overtime request.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("hides the inbox from roles that cannot approve", () => {
    auth.user = { ...auth.user, role: "employee", employee_id: WENDY_ID };
    renderWithClient(<ManagerInbox />);

    expect(screen.queryByText("Manager approval")).not.toBeInTheDocument();
  });
});
