/**
 * Query-cache keys and invalidation helpers for the users feature.
 *
 * The generated client keys queries by URL (`getListUsersQueryKey()` →
 * `["/api/v1/users", params]`), so mutations invalidate by the URL prefix and
 * every parameter variation refreshes.
 */

import type { QueryClient } from "@tanstack/react-query";
import { getListUsersQueryKey } from "@workpulse/api-client";

import { invalidateCurrentUser } from "@/features/auth/api";

export const usersQueryKey = [...getListUsersQueryKey()];

export function invalidateUsers(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: usersQueryKey });
}

/**
 * A user mutation can always target the signed-in account (own role, own
 * activation), so it refreshes the current-user cache too — the nav reflects
 * a role change on the next request, exactly like the API's per-request
 * session resolution.
 */
export async function invalidateUserAggregates(queryClient: QueryClient): Promise<void> {
  await Promise.all([invalidateUsers(queryClient), invalidateCurrentUser(queryClient)]);
}
