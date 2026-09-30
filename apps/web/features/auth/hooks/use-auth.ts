/**
 * Auth data hooks — thin wrappers over the generated client hooks.
 *
 * `useCurrentUser` never retries: a 401 is a final answer (the session is
 * gone), not a transient failure. Login invalidates the current-user cache so
 * the dashboard shell refetches with the fresh cookie; logout clears the whole
 * cache — every cached surface is tenant/user-scoped and must not leak into
 * the next session. `ApiError.code` maps to static, user-facing messages
 * (`.ai/conventions.md`); login failures stay on the page (spec AC13).
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useGetCurrentUser,
  useLogin as useLoginMutation,
  useLogout as useLogoutMutation,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useRouter } from "next/navigation";

/** The signed-in user — the session resolves exactly like every API call. */
export function useCurrentUser() {
  // Plain call: the app-wide `retry: 1` applies; on a 401 the retry lands on
  // the same failure and the gate acts on the final error immediately.
  return useGetCurrentUser();
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useLoginMutation({
    mutation: {
      onSuccess: async () => {
        // Every cached surface is tenant/user-scoped: clear the previous
        // session's rows wholesale so the next login can never be served
        // them, exactly like the logout path.
        queryClient.clear();
      },
    },
  });
}

export function useSignOut() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return useLogoutMutation({
    mutation: {
      onSuccess: async () => {
        // Cached employees/attendance/users belong to the signed-out session.
        queryClient.clear();
        router.push("/login");
        router.refresh();
      },
    },
  });
}

/** A stable, user-facing message per auth failure (never the raw error). */
export function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "invalid_credentials":
        return "Email or password is incorrect.";
      default:
        return "Something went wrong. Please try again.";
    }
  }
  return "Something went wrong. Please try again.";
}
