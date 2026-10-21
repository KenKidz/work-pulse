/**
 * Query-cache keys and invalidation helpers for the payroll workspace.
 *
 * The generated client keys queries by URL (plus params), so invalidating by
 * the parameterless base key refreshes every variation: the run list, a run's
 * detail, its timesheets and its payslips. The config and deduction keys are
 * separate because changing an input never rewrites an already-calculated
 * run — the user recalculates explicitly.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getGetPayrollConfigQueryKey,
  getGetPayrollRunQueryKey,
  getListPayrollDeductionsQueryKey,
  getListPayrollRunsQueryKey,
  getListRunPayslipsQueryKey,
  getListRunTimesheetsQueryKey,
  getListSalariesQueryKey,
} from "@workpulse/api-client";

export const payrollConfigQueryKey = [...getGetPayrollConfigQueryKey()];
export const payrollDeductionsQueryKey = [...getListPayrollDeductionsQueryKey()];
export const payrollSalariesQueryKey = [...getListSalariesQueryKey()];
export const payrollRunsQueryKey = [...getListPayrollRunsQueryKey()];

export function payrollRunQueryKey(id: string): readonly unknown[] {
  return [...getGetPayrollRunQueryKey(id)];
}

export function payrollRunTimesheetsQueryKey(id: string): readonly unknown[] {
  return [...getListRunTimesheetsQueryKey(id)];
}

export function payrollRunPayslipsQueryKey(id: string): readonly unknown[] {
  return [...getListRunPayslipsQueryKey(id)];
}

export function invalidatePayrollConfig(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: payrollConfigQueryKey });
}

export function invalidatePayrollDeductions(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: payrollDeductionsQueryKey });
}

export function invalidatePayrollSalaries(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: payrollSalariesQueryKey });
}

/**
 * A lifecycle transition touches the list and every read of that run. The
 * per-run keys are prefixes, so the paged variants settle together too.
 */
export async function invalidatePayrollRun(queryClient: QueryClient, id: string): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: payrollRunsQueryKey }),
    queryClient.invalidateQueries({ queryKey: payrollRunQueryKey(id) }),
    queryClient.invalidateQueries({ queryKey: payrollRunTimesheetsQueryKey(id) }),
    queryClient.invalidateQueries({ queryKey: payrollRunPayslipsQueryKey(id) }),
  ]);
}

/**
 * Input-only changes (bonuses) leave the calculated payslips and timesheets
 * untouched until an explicit recalculate, so only the run summary needs a
 * refresh.
 */
export async function invalidatePayrollRunSummary(
  queryClient: QueryClient,
  id: string,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: payrollRunsQueryKey }),
    queryClient.invalidateQueries({ queryKey: payrollRunQueryKey(id) }),
  ]);
}
