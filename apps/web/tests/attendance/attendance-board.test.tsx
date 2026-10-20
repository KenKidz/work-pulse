/**
 * AttendanceBoard feature tests (AC12) — the generated api-client layer is
 * mocked; hooks, error mapping and the board composition are the real thing.
 * Covers: counts + activity render, the explicit `?date=` param, the check-in
 * flow (payload + invalidation), the open-record check-out path, and the
 * pinned `409 attendance_already_checked_in` visible error state.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useGetAttendanceBoard,
  useListAttendanceRecords,
  useGetCurrentEmployee,
  useCheckIn,
  useCheckOut,
  useListShifts,
  useCreateShift,
  useUpdateShift,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { AttendanceBoard } from "@/features/attendance/components/AttendanceBoard";

import {
  boardState,
  meState,
  recordsState,
  renderWithClient,
  shiftsState,
  wireMutation,
  wireQuery,
} from "./helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getGetAttendanceBoardQueryKey: () => ["/api/v1/attendance/board"],
  getListAttendanceRecordsQueryKey: () => ["/api/v1/attendance/records"],
  getListShiftsQueryKey: () => ["/api/v1/shifts"],
  getGetCurrentEmployeeQueryKey: () => ["/api/v1/employees/me"],
  useGetAttendanceBoard: vi.fn(),
  useListAttendanceRecords: vi.fn(),
  useGetCurrentEmployee: vi.fn(),
  useCheckIn: vi.fn(),
  useCheckOut: vi.fn(),
  useListShifts: vi.fn(),
  useCreateShift: vi.fn(),
  useUpdateShift: vi.fn(),
}));

const TODAY = "2026-09-28";

const ME = {
  id: "01900000-0000-7000-8000-00000000a001",
  tenant_id: "00000000-0000-4000-8000-000000000001",
  employee_code: "EMP-00001",
  first_name: "Alice",
  last_name: "Adams",
  email: "alice@example.com",
  department_id: "01900000-0000-7000-8000-000000000d01",
  position_id: "01900000-0000-7000-8000-000000000201",
  location_id: "01900000-0000-7000-8000-000000000301",
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2026-01-15",
  terminated_at: null,
  created_at: "2026-01-15T00:00:00Z",
  updated_at: "2026-01-15T00:00:00Z",
};

const BOARD = {
  date: TODAY,
  counts: {
    present: 3,
    remote: 1,
    late: 2,
    on_leave: 0,
    absent: 1,
    checked_out: 4,
  },
  activity: [
    {
      occurred_at: "2026-09-28T02:05:00Z",
      employee_id: "01900000-0000-7000-8000-00000000a002",
      action: "checked_in",
    },
    {
      occurred_at: "2026-09-28T01:00:00Z",
      employee_id: "01900000-0000-7000-8000-00000000a003",
      action: "checked_out",
    },
  ],
};

const OPEN_RECORD = {
  id: "01900000-0000-7000-8000-00000000r001",
  tenant_id: ME.tenant_id,
  employee_id: ME.id,
  workday: TODAY,
  checked_in_at: "2026-09-28T02:10:00Z",
  checked_out_at: null,
  work_mode: "remote",
  late: true,
  shift_id: null,
  check_in_note: null,
  check_out_note: null,
  duration_minutes: null,
  created_at: "2026-09-28T02:10:00Z",
};

const checkInMutate = vi.fn<(variables: unknown) => Promise<unknown>>();

function wireDefaults() {
  wireQuery(useGetAttendanceBoard, boardState(BOARD));
  wireQuery(useListAttendanceRecords, recordsState([]));
  wireQuery(useGetCurrentEmployee, meState(ME));
  wireQuery(useListShifts, shiftsState([]));
  wireMutation(useCheckIn, checkInMutate);
  wireMutation(useCheckOut, vi.fn());
  wireMutation(useCreateShift, vi.fn());
  wireMutation(useUpdateShift, vi.fn());
}

function renderBoard() {
  return renderWithClient(<AttendanceBoard />);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({
    now: new Date("2026-09-28T11:00:00Z"),
    shouldAdvanceTime: true,
  });
  wireDefaults();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AttendanceBoard", () => {
  it("renders the six counts and the activity feed", async () => {
    renderBoard();

    await screen.findByTestId("activity-feed");
    expect(screen.getByTestId("count-present")).toHaveTextContent("3");
    expect(screen.getByTestId("count-remote")).toHaveTextContent("1");
    expect(screen.getByTestId("count-late")).toHaveTextContent("2");
    expect(screen.getByTestId("count-on-leave")).toHaveTextContent("0");
    expect(screen.getByTestId("count-absent")).toHaveTextContent("1");
    expect(screen.getByTestId("count-checked-out")).toHaveTextContent("4");

    const feed = screen.getByTestId("activity-feed");
    expect(feed).toHaveTextContent("checked in");
    expect(feed).toHaveTextContent("checked out");
  });

  it("documents the pinned copy: overlapping counts, absent over-count, on_leave semantics", async () => {
    renderBoard();

    expect(
      await screen.findByText(/counts overlap: someone can be on site or remote and also be late/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/may include people on a rest day/i)).toBeInTheDocument();
    expect(
      screen.getByText(/on leave counts approved leave and is excluded from absent/i),
    ).toBeInTheDocument();
  });

  it("sends the explicit board date plus filters to every query", async () => {
    renderBoard();

    await waitFor(() => {
      expect(useGetAttendanceBoard).toHaveBeenCalledWith({ date: TODAY });
      expect(useListAttendanceRecords).toHaveBeenCalledWith(
        expect.objectContaining({ from: TODAY, to: TODAY }),
      );
    });
  });

  it("shows a loading board state", () => {
    wireQuery(useGetAttendanceBoard, boardState(null, { data: undefined, isPending: true }));

    renderBoard();

    expect(screen.getByText("Loading attendance for this date…")).toBeInTheDocument();
  });

  it("renders a forbidden board as the pinned denied state without a retry", async () => {
    wireQuery(
      useGetAttendanceBoard,
      boardState(null, {
        data: undefined,
        isError: true,
        error: new ApiError(403, "forbidden", "Only managers and above read the board."),
      }),
    );

    renderBoard();

    expect(
      await screen.findByText(/managers, hr and admins can view workforce attendance/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("checks in with the form values and invalidates the board + records", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    checkInMutate.mockResolvedValue({
      data: { data: { ...OPEN_RECORD, work_mode: "onsite" }, status: 201 },
    });

    renderBoard();
    await user.click(await screen.findByTestId("work-mode-remote"));
    await user.click(screen.getByRole("button", { name: /check in/i }));

    await waitFor(() =>
      expect(checkInMutate).toHaveBeenCalledWith({
        data: { work_mode: "remote", check_in_note: null },
      }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Checked in"));
    await waitFor(() => expect(useGetAttendanceBoard).toHaveBeenCalledWith({ date: TODAY }));
  });

  it("renders a 409 double check-in as the visible error state (AC12)", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    checkInMutate.mockRejectedValue(apiError409());

    renderBoard();

    await user.click(await screen.findByRole("button", { name: /check in/i }));

    expect(await screen.findByText(/You are already checked in/i)).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalled();
    // The form stays so the user sees the open record after refresh.
    expect(screen.getByRole("button", { name: /check in/i })).toBeInTheDocument();
  });

  it("renders the open record and offers check-out", async () => {
    wireQuery(useListAttendanceRecords, recordsState([OPEN_RECORD]));

    renderBoard();

    const open = await screen.findByTestId("open-record");
    expect(open).toHaveTextContent("You’re checked in remotely and late.");

    await waitFor(() =>
      expect(useListAttendanceRecords).toHaveBeenCalledWith(
        expect.objectContaining({ from: TODAY, to: TODAY }),
      ),
    );
  });

  it("checks out the open record", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const checkOutMutate = vi.fn<(variables: unknown) => Promise<unknown>>();
    checkOutMutate.mockResolvedValue({ data: { data: OPEN_RECORD, status: 200 } });
    wireMutation(useCheckOut, checkOutMutate);
    wireQuery(useListAttendanceRecords, recordsState([OPEN_RECORD]));

    renderBoard();
    await user.click(await screen.findByRole("button", { name: /check out/i }));

    await waitFor(() =>
      expect(checkOutMutate).toHaveBeenCalledWith({
        id: OPEN_RECORD.id,
        data: { check_out_note: null },
      }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Checked out"));
  });
});

/** ApiError with the pinned double-check-in code (AC12's failure fixture). */
function apiError409(): ApiError {
  return new ApiError(
    409,
    "attendance_already_checked_in",
    "An open attendance record already exists for this employee",
  );
}
