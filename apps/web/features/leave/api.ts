/**
 * Query-cache keys and invalidation helpers for the leave feature.
 *
 * The generated client keys queries by URL + params, so invalidating with the
 * parameterless base key refreshes every variation — my requests, the team
 * approval inbox and the balance cards all settle together after a mutation.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getListLeaveBalancesQueryKey,
  getListLeaveRequestsQueryKey,
  getListLeaveTypesQueryKey,
} from "@workpulse/api-client";

export const leaveRequestsQueryKey = [...getListLeaveRequestsQueryKey()];
export const leaveBalancesQueryKey = [...getListLeaveBalancesQueryKey()];
export const leaveTypesQueryKey = [...getListLeaveTypesQueryKey()];

export function invalidateLeaveRequests(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: leaveRequestsQueryKey });
}

export function invalidateLeaveBalances(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: leaveBalancesQueryKey });
}

/** A transition moves the request list and, on approval, the balances. */
export async function invalidateLeaveAggregates(queryClient: QueryClient): Promise<void> {
  await Promise.all([invalidateLeaveRequests(queryClient), invalidateLeaveBalances(queryClient)]);
}
