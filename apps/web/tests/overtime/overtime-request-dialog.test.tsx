/**
 * OvertimeRequestDialog — the pinned create payload, the `409
 * overtime_overlaps` / `overtime_work_date_in_future` refusals rendered on the
 * work-date field (dialog stays open), and the hours bound enforced locally.
 * The generated api-client is mocked; hooks, schema and error mapping are the
 * real thing.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCreateOvertimeRequest } from "@workpulse/api-client";
import { toast } from "sonner";

import { OvertimeRequestDialog } from "@/features/overtime/components/OvertimeRequestDialog";

import { apiError, renderWithClient, wireMutation, type MutateFn } from "../leave/helpers";

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

const createRequest = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog() {
  const onOpenChange = vi.fn();
  renderWithClient(<OvertimeRequestDialog open onOpenChange={onOpenChange} />);
  return { onOpenChange };
}

async function fillForm(
  user: ReturnType<typeof userEvent.setup>,
  workDate = "2026-10-01",
  hours = "2.5",
) {
  // Native date/number inputs sanitize per-keystroke; one change event sets
  // the exact value the form should validate.
  fireEvent.change(screen.getByLabelText("Work date"), { target: { value: workDate } });
  fireEvent.change(screen.getByLabelText("Hours"), { target: { value: hours } });
  await user.type(screen.getByLabelText("Reason (optional)"), "Quarter close");
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useCreateOvertimeRequest as unknown as MutateFn, createRequest);
});

describe("OvertimeRequestDialog", () => {
  it("submits the pinned OvertimeRequestCreate shape and closes", async () => {
    const user = userEvent.setup();
    createRequest.mockResolvedValue({ data: { status: 201 } });
    const { onOpenChange } = renderDialog();

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    await waitFor(() =>
      expect(createRequest).toHaveBeenCalledWith({
        data: {
          work_date: "2026-10-01",
          hours: 2.5,
          reason: "Quarter close",
        },
      }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toast.success).toHaveBeenCalledWith("Overtime request submitted");
  });

  it("renders a 409 overlap as a work-date error and keeps the dialog open", async () => {
    const user = userEvent.setup();
    createRequest.mockRejectedValue(
      apiError(409, "overtime_overlaps", "Overtime request overlaps an existing request"),
    );
    const { onOpenChange } = renderDialog();

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    expect(
      await screen.findByText("You already have an open overtime request for that date."),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("renders a 409 future work date on the work-date field", async () => {
    const user = userEvent.setup();
    createRequest.mockRejectedValue(
      apiError(409, "overtime_work_date_in_future", "Overtime work date is in the future"),
    );
    renderDialog();

    await fillForm(user, "2030-11-04");
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    expect(
      await screen.findByText(
        "Choose today or an earlier date — overtime is claimed for hours already worked.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("blocks more than 12 hours without calling the API", async () => {
    const user = userEvent.setup();
    renderDialog();

    await fillForm(user, "2026-10-01", "13");
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    expect(await screen.findByText("Hours must be at most 12")).toBeInTheDocument();
    expect(createRequest).not.toHaveBeenCalled();
  });

  it("blocks more than two decimal places", async () => {
    const user = userEvent.setup();
    renderDialog();

    await fillForm(user, "2026-10-01", "1.234");
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    expect(await screen.findByText("Use hours with at most two decimals")).toBeInTheDocument();
    expect(createRequest).not.toHaveBeenCalled();
  });
});
