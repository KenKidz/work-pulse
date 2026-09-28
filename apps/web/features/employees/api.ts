/**
 * Query-cache keys and invalidation helpers for the employees feature.
 *
 * The generated client keys queries by URL (`getListEmployeesQueryKey()` →
 * `["/api/v1/employees", params]`), so mutations invalidate by the URL prefix
 * and every parameter variation refreshes.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getGetEmployeeQueryKey,
  getListEmployeesQueryKey,
  getListEmploymentsQueryKey,
} from "@workpulse/api-client";

export const employeesQueryKey = [...getListEmployeesQueryKey()];

export function employeeDetailQueryKey(id: string): string[] {
  return [...getGetEmployeeQueryKey(id)];
}

export function employmentsQueryKey(id: string): string[] {
  return [...getListEmploymentsQueryKey(id)];
}

export function invalidateEmployees(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: employeesQueryKey });
}

export function invalidateEmployee(queryClient: QueryClient, id: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: employeeDetailQueryKey(id),
  });
}

export function invalidateEmployments(queryClient: QueryClient, id: string): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: employmentsQueryKey(id) });
}

/** A transfer/termination touches the detail, the history and every list. */
export async function invalidateEmployeeAggregates(
  queryClient: QueryClient,
  id: string,
): Promise<void> {
  await Promise.all([
    invalidateEmployees(queryClient),
    invalidateEmployee(queryClient, id),
    invalidateEmployments(queryClient, id),
  ]);
}
