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
import { toast } from "sonner";

import { invalidateOvertimeRequests } from "../api";
import {
  OVERTIME_ERROR_MESSAGES,
  isOvertimeDeciding,
  isOvertimePayrollBound,
  overtimeErrorMessage,
} from "../labels";
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

/** Every pinned failure code has a sentence (labels.ts owns the dictionary). */
export { OVERTIME_ERROR_MESSAGES, overtimeErrorMessage };

export type OvertimeFieldName = "work_date" | "hours" | "reason" | "root";

export type OvertimeFieldErrors = Partial<Record<OvertimeFieldName, string>>;

/** Map a pinned submit error code to the field/dialog that renders it. */
export function overtimeFieldErrors(error: unknown): OvertimeFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "overtime_overlaps":
        return {
          work_date: "You already have an open overtime request for that date.",
        };
      case "overtime_work_date_in_future":
        return {
          work_date:
            "Choose today or an earlier date — overtime is claimed for hours already worked.",
        };
      case "employee_not_active":
        return {
          root: "Your employee record isn’t active, so overtime can’t be claimed.",
        };
      case "employee_not_found":
        return { root: "No employee record is linked to your account." };
      case "forbidden":
        return { root: "You don’t have permission to claim overtime." };
      default:
        return { root: overtimeErrorMessage(error) };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

/** Decision failures render inside the approve/verify/reject dialog. */
export function overtimeDecisionError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        // Role-denied attempts never reach a decision surface; a 403 here is
        // the server refusing self-decision (the requester is the actor).
        return "You can’t decide your own overtime request.";
      case "overtime_not_requested":
      case "overtime_not_manager_approved":
      case "overtime_not_open":
        return overtimeErrorMessage(error);
      case "overtime_request_not_found":
        return OVERTIME_ERROR_MESSAGES.overtime_request_not_found ?? "Request not found.";
      default:
        return overtimeErrorMessage(error);
    }
  }
  return "Something went wrong. Please try again.";
}

/** Withdraw failures render above the request table. */
export function overtimeWithdrawError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        return "You can only withdraw your own overtime request.";
      case "overtime_not_open":
      case "overtime_request_not_found":
        return overtimeErrorMessage(error);
      default:
        return overtimeErrorMessage(error);
    }
  }
  return "Something went wrong. Please try again.";
}

export function useCreateOvertimeAction() {
  const queryClient = useQueryClient();
  return useCreateOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success("Overtime request submitted");
      },
      onError: (error) => toast.error(overtimeErrorMessage(error)),
    },
  });
}

export function useApproveOvertimeAction() {
  const queryClient = useQueryClient();
  return useApproveOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success("Overtime approved");
      },
      onError: (error) => toast.error(overtimeErrorMessage(error)),
    },
  });
}

export function useVerifyOvertimeAction() {
  const queryClient = useQueryClient();
  return useVerifyOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success("Overtime verified");
      },
      onError: (error) => toast.error(overtimeErrorMessage(error)),
    },
  });
}

export function useRejectOvertimeAction() {
  const queryClient = useQueryClient();
  return useRejectOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success("Overtime rejected");
      },
      onError: (error) => toast.error(overtimeErrorMessage(error)),
    },
  });
}

export function useWithdrawOvertimeAction() {
  const queryClient = useQueryClient();
  return useWithdrawOvertimeRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateOvertimeRequests(queryClient);
        toast.success("Overtime request withdrawn");
      },
      onError: (error) => toast.error(overtimeErrorMessage(error)),
    },
  });
}
