/**
 * Query-cache keys and invalidation helpers for the auth feature.
 *
 * The generated client keys the current-user query by URL
 * (`getGetCurrentUserQueryKey()` → `["/api/v1/auth/me"]`), so login, logout
 * and any user mutation that could touch the signed-in account invalidate by
 * that key and every consumer (`AuthGate`, nav filtering) refetches.
 */

import type { QueryClient } from "@tanstack/react-query";
import { getGetCurrentUserQueryKey } from "@workpulse/api-client";

export const currentUserQueryKey = [...getGetCurrentUserQueryKey()];

export function invalidateCurrentUser(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: currentUserQueryKey });
}
