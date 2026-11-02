"use client";

/**
 * Overtime data hooks — thin wrappers over the generated client hooks.
 *
 * Lists poll while a peer decision is pending (`requested`/`manager_approved`)
 * at the fast cadence so a decision made by another user lands without a
 * manual refresh; `hr_verified` rows use the slow cadence because inclusion is
 * payroll's monthly accounting flip — the payroll run polling precedent, no
 * websocket (realtime deltas are out of scope for overtime). Decision queues
 * keep polling when empty so their first cross-user row is discovered.
 * Mutations invalidate the URL-keyed request caches and toast the outcome;
 * `ApiError.code` maps back to visible form/dialog errors so the server stays
 * the authority.
 */

import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useApproveOvertimeRequest,
  useCreateOvertimeRequest,
  useGetCurrentEmployee,
  useListEmployees,
  useListOvertimeRequests,
  useRejectOvertimeRequest,
  useVerifyOvertimeRequest,
  useWithdrawOvertimeRequest,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidateOvertimeRequests } from "../api";
import {
  sharedApiErrorMessage,
  useApiErrorTranslator,
  type SharedApiErrorTranslator,
} from "@/lib/api-errors";
import { isOvertimeDeciding, isOvertimePayrollBound } from "../labels";
import type { ListOvertimeRequestsParams } from "../types";

/** The decision cadence: a peer is waiting on the row. */
export const OVERTIME_POLL_INTERVAL = 2500;

/** The payroll cadence: an `hr_verified` row awaits a monthly accounting flip. */
export const OVERTIME_POLL_SLOW_INTERVAL = 15000;

type OvertimeEmptyPoll = "fast" | "slow";

type OvertimeListOptions = {
  enabled?: boolean;
  /**
   * Keep polling when the current page is empty. Decision queues use `"fast"`
   * so the first request/approval from another user appears without a manual
   * refresh; the verified window uses `"slow"` because it only changes when a
   * peer verifies or payroll finalizes.
   */
  pollWhenEmpty?: OvertimeEmptyPoll;
};

export function useOvertimeRequests(
  params?: ListOvertimeRequestsParams,
  { enabled = true, pollWhenEmpty }: OvertimeListOptions = {},
) {
  return useListOvertimeRequests(params, {
    query: {
      enabled,
      refetchInterval: (query) => {
        const response = query.state.data;
        if (response === undefined || response.status !== 200) return false;
        const items = response.data.items;
        if (items.some((request) => isOvertimeDeciding(request.status))) {
          return OVERTIME_POLL_INTERVAL;
        }
        if (items.some((request) => isOvertimePayrollBound(request.status))) {
          return OVERTIME_POLL_SLOW_INTERVAL;
        }
        if (pollWhenEmpty === "fast") return OVERTIME_POLL_INTERVAL;
        if (pollWhenEmpty === "slow") return OVERTIME_POLL_SLOW_INTERVAL;
        return false;
      },
    },
  });
}

/** The employee directory for names in the inboxes (ids on the wire). */
export function useOvertimeDirectory({
  enabled = true,
  hasEmployeeLink = true,
}: {
  enabled?: boolean;
  hasEmployeeLink?: boolean;
} = {}): ReadonlyMap<string, string> {
  const employeesQuery = useListEmployees({ page_size: 100 }, { query: { enabled } });
  const meQuery = useGetCurrentEmployee({
    query: { enabled: enabled && hasEmployeeLink },
  });

  return useMemo(() => {
    const names = new Map<string, string>();
    const employees = employeesQuery.data?.status === 200 ? employeesQuery.data.data.items : [];
    for (const employee of employees) {
      names.set(employee.id, `${employee.first_name} ${employee.last_name}`.trim());
    }
    const me = meQuery.data?.status === 200 ? meQuery.data.data : null;
    if (me !== null) {
      names.set(me.id, `${me.first_name} ${me.last_name}`.trim());
    }
    return names;
  }, [employeesQuery.data, meQuery.data]);
}

export type OvertimeFieldName = "work_date" | "hours" | "reason" | "root";

export type OvertimeFieldErrors = Partial<Record<OvertimeFieldName, string>>;

type FieldErrorTranslator = (
  key:
    | "overlaps"
    | "futureDate"
    | "employeeNotActive"
    | "employeeNotFound"
    | "forbiddenClaim"
    | "generic",
) => string;

/** Map a pinned submit error code to the field/dialog that renders it. */
export function overtimeFieldErrors(error: unknown, t: FieldErrorTranslator): OvertimeFieldErrors {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return { root: shared };
  if (error instanceof ApiError) {
    switch (error.code) {
      case "overtime_overlaps":
        return { work_date: t("overlaps") };
      case "overtime_work_date_in_future":
        return { work_date: t("futureDate") };
      case "employee_not_active":
        return { root: t("employeeNotActive") };
      case "employee_not_found":
        return { root: t("employeeNotFound") };
      case "forbidden":
        return { root: t("forbiddenClaim") };
      default:
        return { root: error.message };
    }
  }
  return { root: t("generic") };
}

type DecisionErrorTranslator = (
  key:
    | "selfDecision"
    | "notRequested"
    | "notManagerApproved"
    | "notOpen"
    | "requestNotFound"
    | "generic",
) => string;

/** Decision failures render inside the approve/verify/reject dialog. */
export function overtimeDecisionError(error: unknown, t: DecisionErrorTranslator): string {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return shared;
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        // Role-denied attempts never reach a decision surface; a 403 here is
        // the server refusing self-decision (the requester is the actor).
        return t("selfDecision");
      case "overtime_not_requested":
        return t("notRequested");
      case "overtime_not_manager_approved":
        return t("notManagerApproved");
      case "overtime_not_open":
        return t("notOpen");
      case "overtime_request_not_found":
        return t("requestNotFound");
      default:
        if (error.status === 403) return t("selfDecision");
        if (error.status === 404) return t("requestNotFound");
        return error.message;
    }
  }
  return t("generic");
}

type WithdrawErrorTranslator = (
  key: "forbiddenWithdraw" | "notOpen" | "requestNotFound" | "generic",
) => string;

/** Withdraw failures render above the request table. */
export function overtimeWithdrawError(error: unknown, t: WithdrawErrorTranslator): string {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return shared;
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        return t("forbiddenWithdraw");
      case "overtime_not_open":
        return t("notOpen");
      case "overtime_request_not_found":
        return t("requestNotFound");
      default:
        if (error.status === 403) return t("forbiddenWithdraw");
        if (error.status === 404) return t("requestNotFound");
        return error.message;
    }
  }
  return t("generic");
}

type ErrorTranslator = (key: "generic") => string;

function errorMessage(error: unknown, t: ErrorTranslator): string {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return shared;
  return error instanceof ApiError ? error.message : t("generic");
}

export function useCreateOvertimeAction() {
  const t = useTranslations("overtime.toasts");
  const errors = useApiErrorTranslator("overtime.errors");
  const queryClient = useQueryClient();
  return useCreateOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success(t("submitted"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useApproveOvertimeAction() {
  const t = useTranslations("overtime.toasts");
  const errors = useApiErrorTranslator("overtime.errors");
  const queryClient = useQueryClient();
  return useApproveOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success(t("approved"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useVerifyOvertimeAction() {
  const t = useTranslations("overtime.toasts");
  const errors = useApiErrorTranslator("overtime.errors");
  const queryClient = useQueryClient();
  return useVerifyOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success(t("verified"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useRejectOvertimeAction() {
  const t = useTranslations("overtime.toasts");
  const errors = useApiErrorTranslator("overtime.errors");
  const queryClient = useQueryClient();
  return useRejectOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success(t("rejected"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}

export function useWithdrawOvertimeAction() {
  const t = useTranslations("overtime.toasts");
  const errors = useApiErrorTranslator("overtime.errors");
  const queryClient = useQueryClient();
  return useWithdrawOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success(t("withdrawn"));
      },
      onError: (error) => toast.error(errorMessage(error, errors)),
    },
  });
}
