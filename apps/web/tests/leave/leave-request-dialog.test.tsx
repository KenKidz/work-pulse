/**
 * LeaveRequestDialog — submit happy path, the pinned `409 leave_overlaps`
 * rendered as a visible field error (dialog stays open), and the local
 * date-order validation. The generated api-client is mocked; hooks, schema
 * and error mapping are the real thing.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCreateLeaveRequest, useListLeaveTypes } from "@workpulse/api-client";
import { toast } from "sonner";

import { LeaveRequestDialog } from "@/features/leave/components/LeaveRequestDialog";

import {
  apiError,
  listState,
  renderWithClient,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "./helpers";

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

const ANNUAL = {
  id: "01900000-0000-7000-8000-00000000t001",
  code: "annual" as const,
  name: "Annual leave",
  counts_against_balance: true,
};

const UNPAID = {
  id: "01900000-0000-7000-8000-00000000t003",
  code: "unpaid" as const,
  name: "Unpaid leave",
  counts_against_balance: false,
};

const createRequest = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog() {
  const onOpenChange = vi.fn();
  renderWithClient(<LeaveRequestDialog open onOpenChange={onOpenChange} />);
  return { onOpenChange };
}

async function fillForm(
  user: ReturnType<typeof userEvent.setup>,
  start = "2030-11-02",
  end = "2030-11-04",
) {
  await user.click(await screen.findByRole("combobox", { name: "Leave type" }));
  await user.click(await screen.findByRole("option", { name: "Unpaid leave" }));
  await user.type(screen.getByLabelText("Start date"), start);
  await user.type(screen.getByLabelText("End date"), end);
  await user.type(screen.getByLabelText("Reason (optional)"), "Family visit");
}

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useListLeaveTypes, listState([ANNUAL, UNPAID]));
  wireMutation(useCreateLeaveRequest as unknown as MutateFn, createRequest);
});

describe("LeaveRequestDialog", () => {
  it("submits the pinned LeaveRequestCreate shape and closes", async () => {
    const user = userEvent.setup();
    createRequest.mockResolvedValue({ data: { status: 201 } });
    const { onOpenChange } = renderDialog();

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    await waitFor(() =>
      expect(createRequest).toHaveBeenCalledWith({
        data: {
          leave_type_id: UNPAID.id,
          start_date: "2030-11-02",
          end_date: "2030-11-04",
          reason: "Family visit",
        },
      }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toast.success).toHaveBeenCalledWith("Request submitted");
  });

  it("renders a 409 overlap as a start-date error and keeps the dialog open", async () => {
    const user = userEvent.setup();
    createRequest.mockRejectedValue(
      apiError(409, "leave_overlaps", "Leave request overlaps an existing request"),
    );
    const { onOpenChange } = renderDialog();

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    expect(
      await screen.findByText("This range overlaps a pending or approved leave request."),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("blocks an end date before the start date without calling the API", async () => {
    const user = userEvent.setup();
    renderDialog();

    await fillForm(user, "2030-11-04", "2030-11-02");
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    expect(
      await screen.findByText("End date must be on or after the start date"),
    ).toBeInTheDocument();
    expect(createRequest).not.toHaveBeenCalled();
  });
});
