/**
 * Leave data hooks — thin wrappers over the generated client hooks.
 *
 * Queries pass parameters through; mutations invalidate the URL-keyed caches
 * (requests + balances) and toast the outcome. `ApiError.code` maps back to
 * visible form/dialog errors so the server stays the authority — codes are
 * stable, messages never echo server values (attendance/employees pattern).
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useApproveLeaveRequest,
  useCreateLeaveRequest,
  useGetCurrentEmployee,
  useListEmployees,
  useListLeaveBalances,
  useListLeaveRequests,
  useListLeaveTypes,
  useRejectLeaveRequest,
  useWithdrawLeaveRequest,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidateLeaveAggregates } from "../api";
import type { ListLeaveBalancesParams, ListLeaveRequestsParams } from "../types";

export function useLeaveRequests(params?: ListLeaveRequestsParams, enabled = true) {
  return useListLeaveRequests(params, { query: { enabled } });
}

export function useLeaveBalances(params?: ListLeaveBalancesParams, enabled = true) {
  return useListLeaveBalances(params, { query: { enabled } });
}

export function useLeaveTypes() {
  return useListLeaveTypes();
}

/** The employee directory for names in the approval inbox (ids on the wire). */
export function useLeaveDirectory({
  enabled = true,
  hasEmployeeLink = true,
}: {
  enabled?: boolean;
  hasEmployeeLink?: boolean;
} = {}) {
  const employees = useListEmployees({ page_size: 100 }, { query: { enabled } });
  const me = useGetCurrentEmployee({
    query: { enabled: enabled && hasEmployeeLink },
  });
  return { employees, me };
}

type ErrorTranslator = (key: "generic") => string;

export function errorMessage(error: unknown, t: ErrorTranslator): string {
  return error instanceof ApiError ? error.message : t("generic");
}

/** Shared role-denied classification for read surfaces. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "forbidden");
}

export type LeaveFieldName = "leave_type_id" | "start_date" | "end_date" | "root";

export type LeaveFieldErrors = Partial<Record<LeaveFieldName, string>>;

type LeaveErrorTranslator = (
  key:
    | "overlaps"
    | "startTooOld"
    | "insufficientBalance"
    | "typeNotFound"
    | "employeeNotActive"
    | "employeeNotFound"
    | "forbiddenRequest"
    | "generic",
) => string;

/** Map a pinned submit error code to the field/dialog that renders it. */
export function leaveFieldErrors(error: unknown, t: LeaveErrorTranslator): LeaveFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "leave_overlaps":
        return { start_date: t("overlaps") };
      case "leave_start_too_old":
        return { start_date: t("startTooOld") };
      case "leave_insufficient_balance":
        return { root: t("insufficientBalance") };
      case "leave_type_not_found":
        return { leave_type_id: t("typeNotFound") };
      case "employee_not_active":
        return { root: t("employeeNotActive") };
      case "employee_not_found":
        return { root: t("employeeNotFound") };
      case "forbidden":
        return { root: t("forbiddenRequest") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

type DecisionErrorTranslator = (
  key:
    | "selfDecision"
    | "notPending"
    | "balanceChanged"
    | "requestNotFound"
    | "generic",
) => string;

/** Decision failures render inside the approve/reject dialog. */
export function leaveDecisionError(error: unknown, t: DecisionErrorTranslator): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        // Role-denied attempts never reach the inbox; a 403 here is the
        // server refusing self-approval (the request owner is the actor).
        return t("selfDecision");
      case "leave_not_pending":
        return t("notPending");
      case "leave_insufficient_balance":
        return t("balanceChanged");
      case "leave_request_not_found":
        return t("requestNotFound");
      default:
        return error.message;
    }
  }
  return t("generic");
}

type WithdrawErrorTranslator = (
  key: "forbiddenWithdraw" | "notPending" | "requestNotFound" | "generic",
) => string;

/** Withdraw failures render above the request table. */
export function leaveWithdrawError(error: unknown, t: WithdrawErrorTranslator): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        return t("forbiddenWithdraw");
      case "leave_not_pending":
        return t("notPending");
      case "leave_request_not_found":
        return t("requestNotFound");
      default:
        return error.message;
    }
  }
  return t("generic");
}

export function useCreateLeaveAction() {
  const t = useTranslations("leave.toasts");
  const errors = useTranslations("leave.errors");
  const queryClient = useQueryClient();
  return useCreateLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success(t("submitted"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useApproveLeaveAction() {
  const t = useTranslations("leave.toasts");
  const errors = useTranslations("leave.errors");
  const queryClient = useQueryClient();
  return useApproveLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success(t("approved"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useRejectLeaveAction() {
  const t = useTranslations("leave.toasts");
  const errors = useTranslations("leave.errors");
  const queryClient = useQueryClient();
  return useRejectLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success(t("rejected"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useWithdrawLeaveAction() {
  const t = useTranslations("leave.toasts");
  const errors = useTranslations("leave.errors");
  const queryClient = useQueryClient();
  return useWithdrawLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success(t("withdrawn"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
