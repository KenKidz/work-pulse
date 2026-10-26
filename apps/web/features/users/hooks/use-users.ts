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
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidateUserAggregates, invalidateUsers } from "../api";
import type { ListUsersParams } from "../types";

export function useUsers(params?: ListUsersParams) {
  return useListUsers(params);
}

type ErrorTranslator = (key: "generic") => string;

export function errorMessage(error: unknown, t: ErrorTranslator): string {
  return error instanceof ApiError ? error.message : t("generic");
}

export type UserFieldName = "email" | "role" | "employee_id" | "password";

export type UserFieldErrors = Partial<Record<UserFieldName | "root", string>>;

type UserErrorTranslator = (
  key:
    | "emailTaken"
    | "employeeLinkTaken"
    | "employeeNotFound"
    | "lastAdmin"
    | "userNotFound"
    | "forbidden"
    | "generic",
) => string;

/** Map a stable API error code to the field it belongs to (AC12). */
export function userFieldErrors(error: unknown, t: UserErrorTranslator): UserFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "user_email_taken":
        return { email: t("emailTaken") };
      case "employee_link_taken":
        return { employee_id: t("employeeLinkTaken") };
      case "employee_not_found":
        return { employee_id: t("employeeNotFound") };
      case "last_admin":
        return { root: t("lastAdmin") };
      case "user_not_found":
        return { root: t("userNotFound") };
      case "forbidden":
        return { root: t("forbidden") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

export function useUserCreate() {
  const t = useTranslations("users.toasts");
  const errors = useTranslations("users.errors");
  const queryClient = useQueryClient();
  return useCreateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUsers(queryClient);
        toast.success(t("userCreated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useUserUpdate() {
  const t = useTranslations("users.toasts");
  const errors = useTranslations("users.errors");
  const queryClient = useQueryClient();
  return useUpdateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUserAggregates(queryClient);
        toast.success(t("userUpdated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useUserActivate() {
  const t = useTranslations("users.toasts");
  const errors = useTranslations("users.errors");
  const queryClient = useQueryClient();
  return useActivateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUserAggregates(queryClient);
        toast.success(t("userActivated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useUserDeactivate() {
  const t = useTranslations("users.toasts");
  const errors = useTranslations("users.errors");
  const queryClient = useQueryClient();
  return useDeactivateUser({
    mutation: {
      onSuccess: async () => {
        await invalidateUserAggregates(queryClient);
        toast.success(t("userDeactivated"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useUserResetPassword() {
  const t = useTranslations("users.toasts");
  const errors = useTranslations("users.errors");
  // A reset changes no `User` field — it revokes the target's sessions and
  // rehashes, so only a toast is needed (no invalidation).
  return useResetUserPassword({
    mutation: {
      onSuccess: async () => {
        toast.success(t("passwordReset"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
