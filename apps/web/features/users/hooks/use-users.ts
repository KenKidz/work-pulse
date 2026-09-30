/**
 * User-management data hooks — thin wrappers over the generated client hooks.
 *
 * Mutations invalidate the users list and the current user (a mutation can
 * always target the signed-in account) and toast the outcome. `ApiError.code`
 * maps back to form field errors so the server stays the authority
 * (`.ai/conventions.md`).
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useActivateUser,
  useCreateUser,
  useDeactivateUser,
  useListUsers,
  useResetUserPassword,
  useUpdateUser,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { invalidateUserAggregates, invalidateUsers } from "../api";
import type { ListUsersParams } from "../types";

export function useUsers(params?: ListUsersParams) {
  return useListUsers(params);
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

export type UserFieldName = "email" | "role" | "employee_id" | "password";

export type UserFieldErrors = Partial<Record<UserFieldName | "root", string>>;

/** Map a stable API error code to the field it belongs to (AC12). */
export function userFieldErrors(error: unknown): UserFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "user_email_taken":
        return { email: "A user with this email already exists." };
      case "employee_link_taken":
        return { employee_id: "This employee is already linked to a user." };
      case "employee_not_found":
        return { employee_id: "The selected employee no longer exists." };
      case "last_admin":
        return { root: "The last active admin or super admin cannot be deactivated." };
      case "user_not_found":
        return { root: "This user no longer exists." };
      case "forbidden":
        return { root: "You do not have permission to manage users." };
      default:
        return { root: error.message };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function useUserCreate() {
  const queryClient = useQueryClient();
  return useCreateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUsers(queryClient);
        toast.success("User created");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useUserUpdate() {
  const queryClient = useQueryClient();
  return useUpdateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUserAggregates(queryClient);
        toast.success("User updated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useUserActivate() {
  const queryClient = useQueryClient();
  return useActivateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUserAggregates(queryClient);
        toast.success("User activated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useUserDeactivate() {
  const queryClient = useQueryClient();
  return useDeactivateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUserAggregates(queryClient);
        toast.success("User deactivated");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useUserResetPassword() {
  // A reset changes no `User` field — it revokes the target's sessions and
  // rehashes, so only a toast is needed (no invalidation).
  return useResetUserPassword({
    mutation: {
      onSuccess: async () => {
        toast.success("Password reset");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
