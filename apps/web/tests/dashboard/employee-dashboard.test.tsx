/**
 * Employee dashboard composition — the self-service branch must surface a
 * failed attendance read as an error, never as "not checked in" with a live
 * check-in form. The check-in and requests leaves are stubbed so this suite
 * pins the composition and the error branch.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useListAttendanceRecords,
  useListLeaveBalances,
  useListLeaveTypes,
  useListMyPayslips,
} from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { EmployeeDashboard } from "@/features/dashboard/components/EmployeeDashboard";

vi.mock("@workpulse/api-client", () => ({
  useListAttendanceRecords: vi.fn(),
  useListLeaveBalances: vi.fn(),
  useListLeaveTypes: vi.fn(),
  useListMyPayslips: vi.fn(),
}));

vi.mock("@/features/attendance/components/CheckInPanel", () => ({
  CheckInPanel: () => <div data-testid="checkin-panel" />,
}));

vi.mock("@/features/dashboard/components/MyRequestsCard", () => ({
  MyRequestsCard: () => <div data-testid="my-requests-stub" />,
}));

const USER: User = {
  id: "01900000-0000-7000-8000-0000000000b1",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  email: "worker@workpulse.test",
  role: "employee",
  employee_id: "01900000-0000-7000-8000-00000000a001",
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function state(data: unknown) {
  return {
    data,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

let recordsRefetch: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  recordsRefetch = vi.fn();
  vi.mocked(useListAttendanceRecords).mockReturnValue({
    ...state(undefined),
    isError: true,
    error: new Error("records down"),
    refetch: recordsRefetch,
  } as never);
  vi.mocked(useListLeaveBalances).mockReturnValue(state({ status: 200, data: [] }) as never);
  vi.mocked(useListLeaveTypes).mockReturnValue(state({ status: 200, data: [] }) as never);
  vi.mocked(useListMyPayslips).mockReturnValue(
    state({
      status: 200,
      data: {
        items: [],
        page: { page: 1, page_size: 1, total_items: 0, total_pages: 1 },
      },
    }) as never,
  );
});

describe("EmployeeDashboard", () => {
  it("renders a failed records read as an error with a retry, not a check-in form", async () => {
    const user = userEvent.setup();
    render(<EmployeeDashboard user={USER} />);

    expect(screen.getByText("Could not load your attendance status.")).toBeInTheDocument();
    expect(screen.queryByTestId("checkin-panel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(recordsRefetch).toHaveBeenCalled();
  });
});
