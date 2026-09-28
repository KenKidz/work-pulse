/**
 * Query-cache keys and invalidation helpers for the attendance feature.
 *
 * The generated client keys queries by URL (`getGetAttendanceBoardQueryKey()`
 * → `["/api/v1/attendance/board", params]`), so mutations invalidate by the
 * URL prefix and every parameter variation refreshes — board and records both
 * reflect a check-in/out the moment the cache settles (AC12).
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getGetAttendanceBoardQueryKey,
  getGetCurrentEmployeeQueryKey,
  getListAttendanceRecordsQueryKey,
  getListShiftsQueryKey,
} from "@workpulse/api-client";

export const attendanceBoardQueryKey = [...getGetAttendanceBoardQueryKey()];
export const attendanceRecordsQueryKey = [...getListAttendanceRecordsQueryKey()];
export const shiftsQueryKey = [...getListShiftsQueryKey()];

export function invalidateAttendanceBoard(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: attendanceBoardQueryKey });
}

export function invalidateAttendanceRecords(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: attendanceRecordsQueryKey });
}

export function invalidateShifts(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: shiftsQueryKey });
}

/** A check-in or check-out moves the board counts, the feed and the records. */
export async function invalidateAttendanceAggregates(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    invalidateAttendanceBoard(queryClient),
    invalidateAttendanceRecords(queryClient),
  ]);
}

/** Also invalidate the current employee: its presence state drives the panel. */
export function invalidateCurrentEmployee(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: currentEmployeeQueryKey });
}

export const currentEmployeeQueryKey = [...getGetCurrentEmployeeQueryKey()];
