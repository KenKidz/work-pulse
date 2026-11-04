/**
 * ShiftsPanel feature tests — the generated api-client layer is mocked; hooks
 * and the form dialogs are the real thing. Covers: bare-array render, denied
 * query (403) document state, create flow, and the pinned visible server
 * errors (`shift_scope_taken` 409, `shift_time_invalid` 422).
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useListShifts,
  useCreateShift,
  useUpdateShift,
  useListLocations,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { ShiftsPanel } from "@/features/attendance/components/ShiftsPanel";
import type { Shift } from "@/features/attendance/types";

import { recordsState, renderWithClient, shiftsState, wireMutation, wireQuery } from "./helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getGetAttendanceBoardQueryKey: () => ["/api/v1/attendance/board"],
  getListAttendanceRecordsQueryKey: () => ["/api/v1/attendance/records"],
  getGetCurrentEmployeeQueryKey: () => ["/api/v1/employees/me"],
  getListShiftsQueryKey: () => ["/api/v1/shifts"],
  getListDepartmentsQueryKey: () => ["/api/v1/departments"],
  getListPositionsQueryKey: () => ["/api/v1/positions"],
  getListLocationsQueryKey: () => ["/api/v1/locations"],
  useListShifts: vi.fn(),
  useListLocations: vi.fn(),
  useCreateShift: vi.fn(),
  useUpdateShift: vi.fn(),
}));

const DEFAULT_SHIFT: Shift = {
  id: "01900000-0000-7000-8000-000000000s01",
  tenant_id: "00000000-0000-4000-8000-000000000001",
  name: "Standard hours",
  start_time: "09:00",
  end_time: "18:00",
  grace_minutes: 10,
  location_id: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const createShiftMutate = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useListShifts, shiftsState([DEFAULT_SHIFT]));
  wireQuery(useListLocations, recordsState([]));
  wireMutation(useCreateShift, createShiftMutate);
  wireMutation(useUpdateShift, vi.fn());
});

describe("ShiftsPanel", () => {
  it("renders shifts with window, grace and scope", async () => {
    renderWithClient(<ShiftsPanel />);

    expect(await screen.findByText("Standard hours")).toBeInTheDocument();
    expect(screen.getByText("09:00–18:00")).toBeInTheDocument();
    expect(screen.getByText("Default for all locations")).toBeInTheDocument();
    expect(
      screen.getByRole("region", {
        name: "Configured shifts. Scroll horizontally to see locations and actions.",
      }),
    ).toHaveAttribute("tabindex", "0");
    expect(
      screen.getByText("Swipe sideways to see locations and editing actions."),
    ).toBeInTheDocument();
  });

  it("shows the no-shifts document state", async () => {
    wireQuery(useListShifts, shiftsState([]));

    renderWithClient(<ShiftsPanel />);

    expect(
      await screen.findByText(/add a shift to start marking late arrivals/i),
    ).toBeInTheDocument();
  });

  it("renders a denied shifts read (403) as the pinned document state", async () => {
    wireQuery(
      useListShifts,
      shiftsState([], {
        data: undefined,
        isError: true,
        error: new ApiError(403, "forbidden", "Only HR and above manage shifts."),
      }),
    );

    renderWithClient(<ShiftsPanel />);

    expect(
      await screen.findByText(/Only HR, admins and super admins can manage shifts/i),
    ).toBeInTheDocument();
  });

  it("creates a shift and invalidates the list", async () => {
    const user = userEvent.setup();
    createShiftMutate.mockResolvedValue({ data: { data: DEFAULT_SHIFT, status: 201 } });

    renderWithClient(<ShiftsPanel />);
    await user.click(await screen.findByRole("button", { name: /new shift/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Name"), " Night shift");
    await user.click(screen.getByRole("button", { name: /create shift/i }));

    await waitFor(() =>
      expect(createShiftMutate).toHaveBeenCalledWith({
        data: {
          name: " Night shift",
          start_time: "09:00",
          end_time: "18:00",
          grace_minutes: 10,
        },
      }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Shift created"));
  });

  it("renders shift_scope_taken 409 as a visible error (AC-visible server error)", async () => {
    const user = userEvent.setup();
    createShiftMutate.mockRejectedValue(
      new ApiError(409, "shift_scope_taken", "A shift already exists for this scope"),
    );

    renderWithClient(<ShiftsPanel />);
    await user.click(await screen.findByRole("button", { name: /new shift/i }));
    await user.type(screen.getByLabelText("Name"), "Evening shift");
    await user.click(screen.getByRole("button", { name: /create shift/i }));

    expect(
      await screen.findByText(/A shift already exists for this location scope/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("renders shift_time_invalid 422 as a visible error", async () => {
    const user = userEvent.setup();
    createShiftMutate.mockRejectedValue(
      new ApiError(422, "shift_time_invalid", "The shift end time must be after the start time"),
    );

    renderWithClient(<ShiftsPanel />);
    await user.click(await screen.findByRole("button", { name: /new shift/i }));
    await user.type(screen.getByLabelText("Name"), "Backwards shift");
    await user.click(screen.getByRole("button", { name: /create shift/i }));

    expect(
      await screen.findByText(/The shift end time must be after the start time/i),
    ).toBeInTheDocument();
  });
});
