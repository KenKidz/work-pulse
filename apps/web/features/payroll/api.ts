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
  getListMyPayslipsQueryKey,
  getListRunPayslipsQueryKey,
  getListRunTimesheetsQueryKey,
  getListSalariesQueryKey,
} from "@workpulse/api-client";

export const payrollConfigQueryKey = [...getGetPayrollConfigQueryKey()];
export const payrollDeductionsQueryKey = [...getListPayrollDeductionsQueryKey()];
export const payrollSalariesQueryKey = [...getListSalariesQueryKey()];
export const payrollRunsQueryKey = [...getListPayrollRunsQueryKey()];
/** Base key for the self-service payslip list (details need their own match). */
export const payrollPayslipsQueryKey = [...getListMyPayslipsQueryKey()];

/**
 * True for the self-service payslip list and any per-payslip detail key. The
 * generated list key is `["/api/v1/payroll/payslips", ...params]` while a
 * detail key is a single `["/api/v1/payroll/payslips/{id}"]` string, so the
 * two do not share a prefix element — invalidation must match the URL itself.
 */
export function isPayrollPayslipQueryKey(queryKey: readonly unknown[]): boolean {
  const [root] = queryKey;
  if (typeof root !== "string") return false;
  const prefix = payrollPayslipsQueryKey[0];
  return typeof prefix === "string" && (root === prefix || root.startsWith(`${prefix}/`));
}

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
    // Finalize issues payslips; the operator may also be an employee with a
    // self-service list/detail open in another tab. The list and detail keys
    // have different shapes, so match the URL rather than one prefix key.
    queryClient.invalidateQueries({
      predicate: (query) => isPayrollPayslipQueryKey(query.queryKey),
    }),
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
