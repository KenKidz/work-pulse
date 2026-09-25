/**
 * Query-cache keys and invalidation helpers for the organizations feature.
 *
 * The generated client keys queries by URL (`getListDepartmentsQueryKey()` →
 * `["/api/v1/departments", params]`), so mutations invalidate by the URL
 * prefix and every parameter variation refreshes.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getListDepartmentsQueryKey,
  getListLocationsQueryKey,
  getListPositionsQueryKey,
} from "@workpulse/api-client";

export const departmentsQueryKey = [...getListDepartmentsQueryKey()];
export const positionsQueryKey = [...getListPositionsQueryKey()];
export const locationsQueryKey = [...getListLocationsQueryKey()];

export function invalidateDepartments(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: departmentsQueryKey });
}

export function invalidatePositions(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: positionsQueryKey });
}

export function invalidateLocations(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: locationsQueryKey });
}
