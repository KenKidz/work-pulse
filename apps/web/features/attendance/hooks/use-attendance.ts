/**
 * Attendance data hooks — thin wrappers over the generated client hooks.
 *
 * Queries pass parameters through; mutations invalidate the URL-keyed caches
 * (board + records + current employee, whose open state drives the panel) and
 * toast the outcome. `ApiError.code` maps to the visible static messages —
 * codes stay stable across releases, messages never echo server values
 * (pattern from `features/employees/hooks/use-employees.ts`).
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useCheckIn,
  useCheckOut,
  useGetCurrentEmployee,
  useGetAttendanceBoard,
  useListAttendanceRecords,
  useListShifts,
  useCreateShift,
  useUpdateShift,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import {
  invalidateAttendanceAggregates,
  invalidateCurrentEmployee,
  invalidateShifts,
} from "../api";
import type { GetAttendanceBoardParams, ListAttendanceRecordsParams } from "../types";

export function useAttendanceBoard(params?: GetAttendanceBoardParams) {
  return useGetAttendanceBoard(params);
}

export function useAttendanceRecords(params?: ListAttendanceRecordsParams) {
  return useListAttendanceRecords(params);
}

export function useShifts() {
  return useListShifts();
}

export function useCurrentEmployee() {
  return useGetCurrentEmployee();
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

export type CheckFieldName = "work_mode" | "root";

export type AttendanceFieldErrors = Partial<Record<CheckFieldName, string>>;

/** Map a pinned API error code to the visible message it renders (AC12). */
export function attendanceFieldErrors(error: unknown): AttendanceFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "attendance_already_checked_in":
        return { root: "You are already checked in — the open record is shown below." };
      case "attendance_not_open":
        return { root: "There is no open attendance record to check out." };
      case "employee_not_active":
        return { root: "This action is blocked because the employee is not active." };
      case "employee_not_found":
        return { root: "No employee record exists for the signed-in user." };
      case "invalid_work_mode":
        return { root: "The work mode must be onsite or remote." };
      case "shift_scope_taken":
        return { root: "A shift already exists for this location scope." };
      case "shift_time_invalid":
        return { root: "The shift end time must be after the start time." };
      case "shift_not_found":
        return { root: "The selected shift no longer exists." };
      case "location_not_found":
        return { root: "The selected location no longer exists." };
      case "forbidden":
        return { root: "You do not have permission for this attendance action." };
      default:
        return { root: error.message };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function useCheckInAction() {
  const queryClient = useQueryClient();
  return useCheckIn({
    mutation: {
      onSuccess: async () => {
        await invalidateAttendanceAggregates(queryClient);
        await invalidateCurrentEmployee(queryClient);
        toast.success("Checked in");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useCheckOutAction() {
  const queryClient = useQueryClient();
  return useCheckOut({
    mutation: {
      onSuccess: async () => {
        await invalidateAttendanceAggregates(queryClient);
        await invalidateCurrentEmployee(queryClient);
        toast.success("Checked out");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useShiftCreate() {
  const queryClient = useQueryClient();
  return useCreateShift({
    mutation: {
      onSuccess: async () => {
        await invalidateShifts(queryClient);
        toast.success("Shift created");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useShiftUpdate() {
  const queryClient = useQueryClient();
  return useUpdateShift({
    mutation: {
      onSuccess: async () => {
        await invalidateShifts(queryClient);
        toast.success("Shift updated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
