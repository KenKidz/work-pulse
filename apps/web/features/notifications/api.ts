/**
 * Query-cache keys and invalidation helpers for the notification inbox.
 *
 * Every read-state mutation moves two surfaces at once: the inbox list and the
 * header badge. Invalidating the parameterless base keys refreshes every
 * paginated variation and the count together, so the badge and list update
 * without a reload (spec rule 8).
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getGetNotificationUnreadCountQueryKey,
  getListNotificationsQueryKey,
} from "@workpulse/api-client";

export const notificationsQueryKey = [...getListNotificationsQueryKey()];
export const unreadCountQueryKey = [...getGetNotificationUnreadCountQueryKey()];

/** Refresh the inbox list and the header badge after a read-state change. */
export async function invalidateNotifications(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: notificationsQueryKey }),
    queryClient.invalidateQueries({ queryKey: unreadCountQueryKey }),
  ]);
}
