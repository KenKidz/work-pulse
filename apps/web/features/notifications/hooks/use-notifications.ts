/**
 * Notification inbox hooks — thin wrappers over the generated client hooks.
 *
 * The badge opts into window-focus refetch because the global QueryClient
 * disables it (`app/providers.tsx`); that is this surface's pull-based
 * freshness (spec rule 8, decision 7). Both mutations invalidate the list and
 * the count so a read-state change lands on the badge without a reload.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useGetNotificationUnreadCount,
  useListNotifications,
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { invalidateNotifications } from "../api";
import type { ListNotificationsParams } from "../types";

export function useNotifications(params?: ListNotificationsParams, enabled = true) {
  return useListNotifications(params, { query: { enabled } });
}

/**
 * The header badge's only input. The per-query opt-in is deliberate: focus
 * refetch is off globally, and only the badge needs to resync on return.
 */
export function useUnreadCount() {
  return useGetNotificationUnreadCount({ query: { refetchOnWindowFocus: true } });
}

export function notificationErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "notification_not_found":
        return "This notification is no longer available.";
      case "unauthorized":
        return "Your session expired. Sign in again to continue.";
      default:
        return error.message;
    }
  }
  return "Something went wrong. Please try again.";
}

export function useMarkReadAction() {
  const queryClient = useQueryClient();
  return useMarkNotificationRead({
    mutation: {
      onSuccess: async () => {
        await invalidateNotifications(queryClient);
        toast.success("Notification marked read");
      },
      onError: (error) => toast.error(notificationErrorMessage(error)),
    },
  });
}

export function useMarkAllReadAction() {
  const queryClient = useQueryClient();
  return useMarkAllNotificationsRead({
    mutation: {
      onSuccess: async () => {
        await invalidateNotifications(queryClient);
        toast.success("All notifications marked read");
      },
      onError: (error) => toast.error(notificationErrorMessage(error)),
    },
  });
}
