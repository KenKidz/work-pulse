/**
 * AttendanceRecordsTable feature tests — the generated api-client layer is
 * mocked; hooks, filter semantics and table states are the real thing.
 * Covers: rows render (late chip, duration), loading/empty/error, and the
 * filter-drop-not-null regression (employees pattern).
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useListAttendanceRecords } from "@workpulse/api-client";

import {
  RECORDS_PAGE_SIZE,
  AttendanceRecordsTable,
} from "@/features/attendance/components/AttendanceRecordsTable";
import type { AttendanceRecord } from "@/features/attendance/types";

import { recordsState, renderWithClient, wireQuery } from "./helpers";

vi.mock("@workpulse/api-client", () => ({
  getListAttendanceRecordsQueryKey: () => ["/api/v1/attendance/records"],
  getGetAttendanceBoardQueryKey: () => ["/api/v1/attendance/board"],
  getListShiftsQueryKey: () => ["/api/v1/shifts"],
  getGetCurrentEmployeeQueryKey: () => ["/api/v1/employees/me"],
  useListAttendanceRecords: vi.fn(),
  useGetAttendanceBoard: vi.fn(),
  useGetCurrentEmployee: vi.fn(),
  useCheckIn: vi.fn(),
  useCheckOut: vi.fn(),
  useCreateShift: vi.fn(),
  useUpdateShift: vi.fn(),
  useListShifts: vi.fn(),
}));

const RECORD: AttendanceRecord = {
  id: "01900000-0000-7000-8000-00000000r001",
  tenant_id: "00000000-0000-4000-8000-000000000001",
  employee_id: "01900000-0000-7000-8000-00000000a001",
  workday: "2026-09-28",
  checked_in_at: "2026-09-28T02:10:00Z",
  checked_out_at: "2026-09-28T10:43:00Z",
  work_mode: "onsite",
  late: true,
  shift_id: null,
  check_in_note: null,
  check_out_note: null,
  duration_minutes: 513,
  created_at: "2026-09-28T02:10:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useListAttendanceRecords, recordsState([RECORD]));
});

describe("AttendanceRecordsTable", () => {
  it("renders records with mode, lateness chip and closed duration", async () => {
    renderWithClient(<AttendanceRecordsTable />);

    expect(await screen.findByText("On site")).toBeInTheDocument();
    expect(screen.getByText("Late")).toBeInTheDocument();
    expect(screen.getByText("8h 33m")).toBeInTheDocument();
    expect(screen.getByText(/Employee …a001/i)).toBeInTheDocument();
    expect(
      screen.getByRole("region", {
        name: "Attendance records. Scroll horizontally to read all columns.",
      }),
    ).toHaveAttribute("tabindex", "0");
    expect(screen.getByText("Swipe sideways to see every attendance field.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Filter by lateness" })).toHaveClass(
      "w-full",
      "sm:w-36",
    );
    expect(screen.getByRole("textbox", { name: "Filter by employee id" })).toHaveClass(
      "w-full",
      "sm:w-64",
    );
  });

  it("renders an open record's duration as Open", async () => {
    wireQuery(
      useListAttendanceRecords,
      recordsState([{ ...RECORD, checked_out_at: null, duration_minutes: null }]),
    );

    renderWithClient(<AttendanceRecordsTable />);

    expect(await screen.findByText("Open")).toBeInTheDocument();
  });

  it("shows a loading state", () => {
    wireQuery(useListAttendanceRecords, recordsState([], { data: undefined, isPending: true }));

    renderWithClient(<AttendanceRecordsTable />);

    expect(screen.getByText("Loading attendance records…")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    wireQuery(useListAttendanceRecords, recordsState([]));

    renderWithClient(<AttendanceRecordsTable />);

    expect(await screen.findByText(/No attendance records match/i)).toBeInTheDocument();
  });

  it("shows an error state with a retry action", async () => {
    wireQuery(
      useListAttendanceRecords,
      recordsState([], { data: undefined, isError: true, error: new Error("boom") }),
    );

    renderWithClient(<AttendanceRecordsTable />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load attendance records.",
    );
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("applies the from/to date filters and late filter to the query", async () => {
    const user = userEvent.setup();
    renderWithClient(<AttendanceRecordsTable />);

    await user.type(await screen.findByLabelText("From date"), "2026-09-01");
    await user.type(screen.getByLabelText("To date"), "2026-09-28");

    await waitFor(() =>
      expect(useListAttendanceRecords).toHaveBeenCalledWith(
        expect.objectContaining({ from: "2026-09-01", to: "2026-09-28", page: 1 }),
      ),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by lateness" }));
    await user.click(await screen.findByRole("option", { name: "Late only" }));

    await waitFor(() =>
      expect(useListAttendanceRecords).toHaveBeenCalledWith(
        expect.objectContaining({ late: true, page: 1 }),
      ),
    );
  });

  it("clearing a filter drops the parameter instead of sending null (422 regression)", async () => {
    const user = userEvent.setup();
    renderWithClient(<AttendanceRecordsTable />);

    await user.click(await screen.findByRole("combobox", { name: "Filter by lateness" }));
    await user.click(await screen.findByRole("option", { name: "On time only" }));
    await waitFor(() =>
      expect(useListAttendanceRecords).toHaveBeenCalledWith(
        expect.objectContaining({ late: false }),
      ),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by lateness" }));
    await user.click(await screen.findByRole("option", { name: "Any lateness" }));

    await waitFor(() => {
      const last = lastTableCall();
      expect(last).toEqual(expect.objectContaining({ late: undefined }));
      expect(Object.values(last ?? {})).not.toContain(null);
    });
  });
});

/** Most recent call carrying the table's page size (shared mocked hook). */
function lastTableCall(): Record<string, unknown> | undefined {
  const calls = vi.mocked(useListAttendanceRecords).mock.calls.map(([params]) => params) as (
    Record<string, unknown> | undefined
  )[];
  const tableCalls = calls.filter((params) => params && params.page_size === RECORDS_PAGE_SIZE);
  return tableCalls[tableCalls.length - 1];
}
