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
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { invalidatePayrollRun, invalidatePayrollRunSummary, payrollRunsQueryKey } from "../api";
import { isRunInProgress } from "../labels";
import { payrollErrorMessage } from "../errors";

export { bonusFieldErrors, runFieldErrors } from "../errors";
export type {
  BonusFieldErrors,
  BonusFieldName,
  RunFieldErrors,
  RunFieldName,
} from "../errors";
import type {
  ListPayrollRunsParams,
  ListRunPayslipsParams,
  ListRunTimesheetsParams,
} from "../types";

/** One poll cadence for the worker-driven surfaces. */
export const PAYROLL_POLL_INTERVAL = 2500;

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
  const t = useTranslations("payroll.toasts");
  const errors = useTranslations("payroll.errors");
  const queryClient = useQueryClient();
  return useCreatePayrollRun({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: payrollRunsQueryKey });
        toast.success(t("runQueued"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}

export function usePayrollRunRecalculate(runId: string) {
  const t = useTranslations("payroll.toasts");
  const errors = useTranslations("payroll.errors");
  const queryClient = useQueryClient();
  return useRecalculatePayrollRun({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollRun(queryClient, runId);
        toast.success(t("runRecalculated"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}

export function usePayrollRunFinalize(runId: string) {
  const t = useTranslations("payroll.toasts");
  const errors = useTranslations("payroll.errors");
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
        toast.success(t("runFinalized"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}

export function useRunBonusCreate(runId: string) {
  const t = useTranslations("payroll.toasts");
  const errors = useTranslations("payroll.errors");
  const queryClient = useQueryClient();
  return useCreateRunBonus({
    mutation: {
      onSuccess: async () => {
        // A bonus is an input: the payslip set and timesheets only change
        // after an explicit recalculation.
        await invalidatePayrollRunSummary(queryClient, runId);
        toast.success(t("bonusAdded"));
      },
      onError: (error) => toast.error(payrollErrorMessage(error, errors)),
    },
  });
}
