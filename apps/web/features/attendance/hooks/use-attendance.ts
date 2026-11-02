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
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  invalidateAttendanceAggregates,
  invalidateCurrentEmployee,
  invalidateShifts,
} from "../api";
import {
  sharedApiErrorMessage,
  useApiErrorTranslator,
  type SharedApiErrorTranslator,
} from "@/lib/api-errors";
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

type ErrorTranslator = (key: "generic") => string;

export function errorMessage(error: unknown, t: ErrorTranslator): string {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return shared;
  return error instanceof ApiError ? error.message : t("generic");
}

/** Shared role-denied classification: the board and the shifts list both honor it. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "forbidden");
}

export type CheckFieldName = "work_mode" | "root";

export type AttendanceFieldErrors = Partial<Record<CheckFieldName, string>>;

type AttendanceErrorTranslator = (
  key:
    | "alreadyCheckedIn"
    | "notOpen"
    | "employeeNotActive"
    | "employeeNotFound"
    | "invalidWorkMode"
    | "shiftScopeTaken"
    | "shiftTimeInvalid"
    | "shiftNotFound"
    | "locationNotFound"
    | "forbidden"
    | "generic",
) => string;

/** Map a pinned API error code to the visible message it renders (AC12). */
export function attendanceFieldErrors(
  error: unknown,
  t: AttendanceErrorTranslator,
): AttendanceFieldErrors {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return { root: shared };
  if (error instanceof ApiError) {
    switch (error.code) {
      case "attendance_already_checked_in":
        return { root: t("alreadyCheckedIn") };
      case "attendance_not_open":
        return { root: t("notOpen") };
      case "employee_not_active":
        return { root: t("employeeNotActive") };
      case "employee_not_found":
        return { root: t("employeeNotFound") };
      case "invalid_work_mode":
        return { root: t("invalidWorkMode") };
      case "shift_scope_taken":
        return { root: t("shiftScopeTaken") };
      case "shift_time_invalid":
        return { root: t("shiftTimeInvalid") };
      case "shift_not_found":
        return { root: t("shiftNotFound") };
      case "location_not_found":
        return { root: t("locationNotFound") };
      case "forbidden":
        return { root: t("forbidden") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

export function useCheckInAction() {
  const t = useTranslations("attendance.toasts");
  const errors = useApiErrorTranslator("attendance.errors");
  const queryClient = useQueryClient();
  return useCheckIn({
    mutation: {
      onSuccess: async () => {
        await invalidateAttendanceAggregates(queryClient);
        await invalidateCurrentEmployee(queryClient);
        toast.success(t("checkedIn"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useCheckOutAction() {
  const t = useTranslations("attendance.toasts");
  const errors = useApiErrorTranslator("attendance.errors");
  const queryClient = useQueryClient();
  return useCheckOut({
    mutation: {
      onSuccess: async () => {
        await invalidateAttendanceAggregates(queryClient);
        await invalidateCurrentEmployee(queryClient);
        toast.success(t("checkedOut"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useShiftCreate() {
  const t = useTranslations("attendance.toasts");
  const errors = useApiErrorTranslator("attendance.errors");
  const queryClient = useQueryClient();
  return useCreateShift({
    mutation: {
      onSuccess: async () => {
        await invalidateShifts(queryClient);
        toast.success(t("shiftCreated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useShiftUpdate() {
  const t = useTranslations("attendance.toasts");
  const errors = useApiErrorTranslator("attendance.errors");
  const queryClient = useQueryClient();
  return useUpdateShift({
    mutation: {
      onSuccess: async () => {
        await invalidateShifts(queryClient);
        toast.success(t("shiftUpdated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
