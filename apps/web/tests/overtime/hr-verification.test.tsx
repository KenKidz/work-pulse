/**
 * HrVerificationQueue — manager-approved claims verify/reject, verified rows
 * still inside the un-verify window (reject only), self-decision withheld, the
 * wrong-state 409 surfaced in the dialog, and the HR-role gate. The generated
 * api-client is mocked.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useGetCurrentEmployee,
  useListEmployees,
  useListOvertimeRequests,
  useRejectOvertimeRequest,
  useVerifyOvertimeRequest,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { HrVerificationQueue } from "@/features/overtime/components/HrVerificationQueue";
import type { ListOvertimeRequestsParams, OvertimeRequest } from "@/features/overtime/types";

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
const WENDY_ID = "01900000-0000-7000-8000-000000000021";
const PAT_ID = "01900000-0000-7000-8000-000000000022";

const auth = vi.hoisted(() => ({
  user: {
    id: "01900000-0000-7000-8000-000000000015",
    tenant_id: "01900000-0000-7000-8000-000000000001",
    email: "hr@workpulse.test",
    role: "hr",
    employee_id: null as string | null,
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

const WENDY = {
  id: WENDY_ID,
  tenant_id: TENANT_ID,
  first_name: "Wendy",
  last_name: "Worker",
};

const PAT = {
  id: PAT_ID,
  tenant_id: TENANT_ID,
  first_name: "Priya",
  last_name: "Patel",
};

function overtimeRequest(overrides: Partial<OvertimeRequest>): OvertimeRequest {
  return {
    id: "",
    tenant_id: TENANT_ID,
    employee_id: WENDY_ID,
    work_date: "2030-11-02",
    hours: 2.5,
    status: "manager_approved",
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

const PENDING_REQUEST = overtimeRequest({
  id: "01900000-0000-7000-8000-00000000o001",
  employee_id: WENDY_ID,
  work_date: "2030-11-02",
  hours: 2.5,
  status: "manager_approved",
  reason: "Quarter close",
  manager_note: "Approved — busy week.",
});

const VERIFIED_REQUEST = overtimeRequest({
  id: "01900000-0000-7000-8000-00000000o002",
  employee_id: PAT_ID,
  work_date: "2030-11-01",
  hours: 1.25,
  status: "hr_verified",
  manager_note: "Agreed.",
  hr_note: "Confirmed against the shift log.",
});

const verify = vi.fn<(variables: unknown) => Promise<unknown>>();
const reject = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { ...auth.user, role: "hr", employee_id: null };
  vi.mocked(useListOvertimeRequests).mockImplementation(((params?: ListOvertimeRequestsParams) =>
    params?.status === "hr_verified"
      ? requestPageState([VERIFIED_REQUEST])
      : requestPageState([PENDING_REQUEST])) as never);
  wireQuery(useListEmployees, requestPageState([WENDY, PAT]));
  wireQuery(useGetCurrentEmployee, itemState(null));
  wireMutation(useVerifyOvertimeRequest as unknown as MutateFn, verify);
  wireMutation(useRejectOvertimeRequest as unknown as MutateFn, reject);
});

describe("HrVerificationQueue", () => {
  it("lists manager-approved claims first, then the un-verify window rows", async () => {
    renderWithClient(<HrVerificationQueue />);

    expect(await screen.findByText("Wendy Worker")).toBeInTheDocument();
    expect(screen.getByText("Priya Patel")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Awaiting verification" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Verified — payroll un-verify window" }),
    ).toBeInTheDocument();

    const rows = screen.getAllByTestId("hr-verification-row");
    expect(rows.map((row) => row.getAttribute("data-status"))).toEqual([
      "manager_approved",
      "hr_verified",
    ]);
    expect(
      screen.getByRole("button", { name: "Verify overtime request for Wendy Worker" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Verify overtime request for Priya Patel" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reject overtime request for Priya Patel" }),
    ).toBeInTheDocument();
  });

  it("verifies with an optional decision note", async () => {
    const user = userEvent.setup();
    verify.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<HrVerificationQueue />);

    await user.click(
      await screen.findByRole("button", {
        name: "Verify overtime request for Wendy Worker",
      }),
    );
    expect(screen.getByRole("heading", { name: "Verify overtime request" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Decision note (optional)"), "Checked the shift log.");
    await user.click(screen.getByRole("button", { name: "Verify request" }));

    await waitFor(() =>
      expect(verify).toHaveBeenCalledWith({
        id: PENDING_REQUEST.id,
        data: { note: "Checked the shift log." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Overtime verified");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("rejects a verified row inside the payroll un-verify window", async () => {
    const user = userEvent.setup();
    reject.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<HrVerificationQueue />);

    await user.click(
      await screen.findByRole("button", {
        name: "Reject overtime request for Priya Patel",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Reject request" }));

    await waitFor(() =>
      expect(reject).toHaveBeenCalledWith({
        id: VERIFIED_REQUEST.id,
        data: { note: null },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Overtime rejected");
  });

  it("surfaces a wrong-state 409 in the dialog", async () => {
    const user = userEvent.setup();
    verify.mockRejectedValue(
      apiError(409, "overtime_not_manager_approved", "Overtime request is not manager approved"),
    );
    renderWithClient(<HrVerificationQueue />);

    await user.click(
      await screen.findByRole("button", {
        name: "Verify overtime request for Wendy Worker",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Verify request" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This request is no longer waiting for HR verification. Refresh and try again.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("withholds actions on the HR user's own claim", async () => {
    auth.user = { ...auth.user, employee_id: WENDY_ID };
    renderWithClient(<HrVerificationQueue />);

    expect(
      await screen.findByText("Your own request — someone else must decide."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Verify overtime request for Wendy Worker" }),
    ).not.toBeInTheDocument();
  });

  it("hides the queue from managers without an HR role", () => {
    auth.user = { ...auth.user, role: "manager", employee_id: WENDY_ID };
    renderWithClient(<HrVerificationQueue />);

    expect(screen.queryByText("HR verification")).not.toBeInTheDocument();
  });
});
