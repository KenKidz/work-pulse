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

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

/** Shared role-denied classification for read surfaces. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "forbidden");
}

export type LeaveFieldName = "leave_type_id" | "start_date" | "end_date" | "root";

export type LeaveFieldErrors = Partial<Record<LeaveFieldName, string>>;

/** Map a pinned submit error code to the field/dialog that renders it. */
export function leaveFieldErrors(error: unknown): LeaveFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "leave_overlaps":
        return {
          start_date: "This range overlaps a pending or approved leave request.",
        };
      case "leave_start_too_old":
        return {
          start_date:
            "Annual and unpaid leave must start today or later; sick leave can start up to 7 days ago.",
        };
      case "leave_insufficient_balance":
        return {
          root: "This request is longer than the remaining balance for that leave type.",
        };
      case "leave_type_not_found":
        return {
          leave_type_id: "The selected leave type no longer exists.",
        };
      case "employee_not_active":
        return {
          root: "This employee is not active, so leave can’t be requested.",
        };
      case "employee_not_found":
        return { root: "No employee record is linked to your account." };
      case "forbidden":
        return { root: "You don’t have permission to request leave." };
      default:
        return { root: error.message };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

/** Decision failures render inside the approve/reject dialog. */
export function leaveDecisionError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        // Role-denied attempts never reach the inbox; a 403 here is the
        // server refusing self-approval (the request owner is the actor).
        return "You can’t decide your own leave request.";
      case "leave_not_pending":
        return "This request is no longer pending. Refresh the list and try again.";
      case "leave_insufficient_balance":
        return "The employee’s remaining balance no longer covers this request.";
      case "leave_request_not_found":
        return "This request is no longer available.";
      default:
        return error.message;
    }
  }
  return "Something went wrong. Please try again.";
}

/** Withdraw failures render above the request table. */
export function leaveWithdrawError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "forbidden":
        return "You can only withdraw your own leave request.";
      case "leave_not_pending":
        return "This request is no longer pending. Refresh the list and try again.";
      case "leave_request_not_found":
        return "This request is no longer available.";
      default:
        return error.message;
    }
  }
  return "Something went wrong. Please try again.";
}

export function useCreateLeaveAction() {
  const queryClient = useQueryClient();
  return useCreateLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success("Request submitted");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useApproveLeaveAction() {
  const queryClient = useQueryClient();
  return useApproveLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success("Request approved");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useRejectLeaveAction() {
  const queryClient = useQueryClient();
  return useRejectLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success("Request rejected");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}

export function useWithdrawLeaveAction() {
  const queryClient = useQueryClient();
  return useWithdrawLeaveRequest({
    mutation: {
      onSuccess: async () => {
        await invalidateLeaveAggregates(queryClient);
        toast.success("Request withdrawn");
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
}
