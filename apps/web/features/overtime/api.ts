/**
 * Query-cache keys and invalidation helpers for the overtime feature.
 *
 * The generated client keys queries by URL + params, so invalidating with the
 * parameterless base key refreshes every variation — the employee's own list,
 * the manager inbox and the HR verification queues all settle together after a
 * transition.
 */

import type { QueryClient } from "@tanstack/react-query";
import { getListOvertimeRequestsQueryKey } from "@workpulse/api-client";

export const overtimeRequestsQueryKey = [...getListOvertimeRequestsQueryKey()];

export function invalidateOvertimeRequests(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: overtimeRequestsQueryKey });
}
