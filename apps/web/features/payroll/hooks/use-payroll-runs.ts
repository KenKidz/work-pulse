"use client";

/**
 * Run lifecycle and read hooks. A queued or calculating run is polled until it
 * settles — the create endpoint returns `queued`, so the worker's progress is
 * observed through the cache, never a shadow copy in component state. Every
 * transition invalidates the run's own reads and the run list.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useCreatePayrollRun,
  useCreateRunBonus,
  useFinalizePayrollRun,
  useGetPayrollRun,
  useListPayrollRuns,
  useListRunPayslips,
  useListRunTimesheets,
  useRecalculatePayrollRun,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { invalidatePayrollRun, invalidatePayrollRunSummary, payrollRunsQueryKey } from "../api";
import { isRunInProgress, payrollErrorMessage } from "../labels";
import type {
  ListPayrollRunsParams,
  ListRunPayslipsParams,
  ListRunTimesheetsParams,
} from "../types";

/** One poll cadence for the worker-driven surfaces. */
export const PAYROLL_POLL_INTERVAL = 2500;

export type RunFieldName = "period" | "root";

export type RunFieldErrors = Partial<Record<RunFieldName, string>>;

/** Create-run failures belong to the period field; the rest are lifecycle guards. */
export function runFieldErrors(error: unknown): RunFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "payroll_period_not_closed":
        return { period: "That month hasn’t ended yet. Choose a closed month." };
      case "payroll_period_open":
        return { period: "A run for this month is still open. Finalize it first." };
      case "forbidden":
        return { root: "You don’t have permission to run payroll." };
      default:
        return { root: payrollErrorMessage(error) };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export type BonusFieldName = "employee_id" | "amount" | "note" | "root";

export type BonusFieldErrors = Partial<Record<BonusFieldName, string>>;

export function bonusFieldErrors(error: unknown): BonusFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "payroll_employee_not_in_run":
        return { employee_id: "That employee isn’t part of this run." };
      case "payroll_run_finalized":
        return { root: "This run is finalized and can no longer change." };
      case "forbidden":
        return { root: "You don’t have permission to add bonuses." };
      default:
        return { root: payrollErrorMessage(error) };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function usePayrollRuns(params?: ListPayrollRunsParams) {
  return useListPayrollRuns(params, {
    query: {
      refetchInterval: (query) => {
        const response = query.state.data;
        if (response === undefined || response.status !== 200) return false;
        return response.data.items.some((run) => isRunInProgress(run.status))
          ? PAYROLL_POLL_INTERVAL
          : false;
      },
    },
  });
}

export function usePayrollRun(id: string, enabled = true) {
  return useGetPayrollRun(id, {
    query: {
      enabled,
      refetchInterval: (query) => {
        const response = query.state.data;
        if (response === undefined || response.status !== 200) return false;
        return isRunInProgress(response.data.status) ? PAYROLL_POLL_INTERVAL : false;
      },
    },
  });
}

export function useRunTimesheets(id: string, params?: ListRunTimesheetsParams, enabled = true) {
  return useListRunTimesheets(id, params, { query: { enabled } });
}

export function useRunPayslips(id: string, params?: ListRunPayslipsParams, enabled = true) {
  return useListRunPayslips(id, params, { query: { enabled } });
}

export function usePayrollRunCreate() {
  const queryClient = useQueryClient();
  return useCreatePayrollRun({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: payrollRunsQueryKey });
        toast.success("Payroll run queued");
      },
      onError: (error) => toast.error(payrollErrorMessage(error)),
    },
  });
}

export function usePayrollRunRecalculate(runId: string) {
  const queryClient = useQueryClient();
  return useRecalculatePayrollRun({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollRun(queryClient, runId);
        toast.success("Payroll run queued for recalculation");
      },
      onError: (error) => toast.error(payrollErrorMessage(error)),
    },
  });
}

export function usePayrollRunFinalize(runId: string) {
  const queryClient = useQueryClient();
  return useFinalizePayrollRun({
    mutation: {
      onSuccess: async (response) => {
        await invalidatePayrollRun(queryClient, runId);
        // A correction run supersedes the prior finalized run: its detail and
        // payslips changed too, even though they live under a different id.
        if (response.status === 200 && response.data.supersedes_run_id) {
          await invalidatePayrollRun(queryClient, response.data.supersedes_run_id);
        }
        toast.success("Payroll run finalized");
      },
      onError: (error) => toast.error(payrollErrorMessage(error)),
    },
  });
}

export function useRunBonusCreate(runId: string) {
  const queryClient = useQueryClient();
  return useCreateRunBonus({
    mutation: {
      onSuccess: async () => {
        // A bonus is an input: the payslip set and timesheets only change
        // after an explicit recalculation.
        await invalidatePayrollRunSummary(queryClient, runId);
        toast.success("Bonus added. Recalculate the run to include it.");
      },
      onError: (error) => toast.error(payrollErrorMessage(error)),
    },
  });
}
