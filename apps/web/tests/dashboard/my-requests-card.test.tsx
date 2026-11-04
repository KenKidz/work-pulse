/**
 * The employee's merged leave + overtime trail. The two scoped lists are mocked
 * at the feature-hook boundary; status labels, ordering and deep links are the
 * behaviour under test.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLeaveRequests } from "@/features/leave/hooks/use-leave";
import { useOvertimeRequests } from "@/features/overtime/hooks/use-overtime";
import { MyRequestsCard } from "@/features/dashboard/components/MyRequestsCard";

vi.mock("@/features/leave/hooks/use-leave", () => ({
  useLeaveRequests: vi.fn(),
}));

vi.mock("@/features/overtime/hooks/use-overtime", () => ({
  useOvertimeRequests: vi.fn(),
}));

const LEAVE = {
  id: "leave-1",
  employee_id: "employee-1",
  leave_type_id: "type-annual",
  leave_type_code: "annual",
  start_date: "2026-11-12",
  end_date: "2026-11-14",
  days: 3,
  status: "pending",
  reason: "Family trip",
  created_at: "2026-11-04T01:00:00Z",
};

const OVERTIME = {
  id: "ot-1",
  employee_id: "employee-1",
  work_date: "2026-11-03",
  hours: 3.5,
  status: "requested",
  reason: "Release support",
  created_at: "2026-11-04T00:30:00Z",
};

function queryState(items: unknown[]) {
  return {
    data: {
      status: 200,
      data: {
        items,
        page: { page: 1, page_size: 5, total_items: items.length, total_pages: 1 },
      },
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useLeaveRequests).mockReturnValue(queryState([LEAVE]) as never);
  vi.mocked(useOvertimeRequests).mockReturnValue(queryState([OVERTIME]) as never);
});

describe("MyRequestsCard", () => {
  it("merges leave and overtime with their own statuses and links", () => {
    render(<MyRequestsCard enabled />);

    expect(screen.getByText("Annual leave")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("Release support")).toBeInTheDocument();
    expect(screen.getByText("Requested")).toBeInTheDocument();
    expect(screen.getByText(/3.5 h/)).toBeInTheDocument();

    expect(screen.getByRole("link", { name: /Annual leave/ })).toHaveAttribute("href", "/leave");
    expect(screen.getByRole("link", { name: /Release support/ })).toHaveAttribute(
      "href",
      "/overtime",
    );
  });

  it("invites the first request when both lists are empty", () => {
    vi.mocked(useLeaveRequests).mockReturnValue(queryState([]) as never);
    vi.mocked(useOvertimeRequests).mockReturnValue(queryState([]) as never);

    render(<MyRequestsCard enabled />);

    expect(screen.getByText(/No requests yet. Start with a leave request/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Request leave" })).toHaveAttribute("href", "/leave");
  });
});
