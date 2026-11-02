/**
 * Stable payroll codes → localized sentences and field placements. One place
 * so every toast, dialog and inline error shares wording, and no `payroll_*`
 * code can leak into the interface.
 */

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { sharedApiErrorMessage, type SharedApiErrorTranslator } from "@/lib/api-errors";

export type PayrollErrorKey =
  | "periodNotClosed"
  | "periodOpen"
  | "runNotCalculated"
  | "runFinalized"
  | "employeeNotInRun"
  | "salaryDateTaken"
  | "deductionCodeTaken"
  | "deductionNotFound"
  | "runStale"
  | "runNotFound"
  | "payrollNotFound"
  | "payslipNotFound"
  | "employeeNotFound"
  | "validation"
  | "forbidden"
  | "generic"
  | "forbiddenRun"
  | "forbiddenBonus"
  | "forbiddenDeduction"
  | "forbiddenSalary"
  | "runFieldNotClosed"
  | "runFieldOpen"
  | "employeeNotInRunShort"
  | "deductionValueInvalid"
  | "salaryDateTakenField";

export type PayrollErrorTranslator = (key: PayrollErrorKey) => string;

/** A stable, user-facing sentence for a payroll failure (never a raw code). */
export function payrollErrorMessage(error: unknown, t: PayrollErrorTranslator): string {
  const shared = sharedApiErrorMessage(error, t as unknown as SharedApiErrorTranslator);
  if (shared !== null) return shared;
  if (error instanceof ApiError) {
    switch (error.code) {
      case "payroll_period_not_closed":
        return t("periodNotClosed");
      case "payroll_period_open":
        return t("periodOpen");
      case "payroll_run_not_calculated":
        return t("runNotCalculated");
      case "payroll_run_finalized":
        return t("runFinalized");
      case "payroll_employee_not_in_run":
        return t("employeeNotInRun");
      case "salary_effective_date_taken":
        return t("salaryDateTaken");
      case "payroll_deduction_code_taken":
        return t("deductionCodeTaken");
      case "payroll_deduction_not_found":
        return t("deductionNotFound");
      case "payroll_run_stale":
        return t("runStale");
      case "payroll_run_not_found":
        return t("runNotFound");
      case "payroll_not_found":
        return t("payrollNotFound");
      case "payslip_not_found":
        return t("payslipNotFound");
      case "employee_not_found":
        return t("employeeNotFound");
      case "validation_error":
        return t("validation");
      case "forbidden":
        return t("forbidden");
      default:
        if (error.status === 403) return t("forbidden");
        if (error.status === 404) return t("payrollNotFound");
        return error.message;
    }
  }
  return t("generic");
}

type RunFieldTranslator = (
  key: "runFieldNotClosed" | "runFieldOpen" | "forbiddenRun" | "generic",
) => string;

export type RunFieldName = "period" | "root";
export type RunFieldErrors = Partial<Record<RunFieldName, string>>;

/** Create-run failures belong to the period field; the rest are lifecycle guards. */
export function runFieldErrors(error: unknown, t: RunFieldTranslator): RunFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "payroll_period_not_closed":
        return { period: t("runFieldNotClosed") };
      case "payroll_period_open":
        return { period: t("runFieldOpen") };
      case "forbidden":
        return { root: t("forbiddenRun") };
      default:
        return { root: payrollErrorMessage(error, t as PayrollErrorTranslator) };
    }
  }
  return { root: t("generic") };
}

type BonusFieldTranslator = (
  key: "employeeNotInRunShort" | "runFinalized" | "forbiddenBonus" | "generic",
) => string;

export type BonusFieldName = "employee_id" | "amount" | "note" | "root";
export type BonusFieldErrors = Partial<Record<BonusFieldName, string>>;

export function bonusFieldErrors(error: unknown, t: BonusFieldTranslator): BonusFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "payroll_employee_not_in_run":
        return { employee_id: t("employeeNotInRunShort") };
      case "payroll_run_finalized":
        return { root: t("runFinalized") };
      case "forbidden":
        return { root: t("forbiddenBonus") };
      default:
        return { root: payrollErrorMessage(error, t as PayrollErrorTranslator) };
    }
  }
  return { root: t("generic") };
}

type DeductionFieldTranslator = (
  key:
    | "deductionCodeTaken"
    | "deductionValueInvalid"
    | "deductionNotFound"
    | "forbiddenDeduction"
    | "generic",
) => string;

export type DeductionFieldName = "code" | "name" | "value" | "root";
export type DeductionFieldErrors = Partial<Record<DeductionFieldName, string>>;

/** Map a stable deduction error code onto the field that caused it. */
export function deductionFieldErrors(
  error: unknown,
  t: DeductionFieldTranslator,
): DeductionFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "payroll_deduction_code_taken":
        return { code: t("deductionCodeTaken") };
      case "validation_error":
        return { value: t("deductionValueInvalid") };
      case "payroll_deduction_not_found":
        return { root: t("deductionNotFound") };
      case "forbidden":
        return { root: t("forbiddenDeduction") };
      default:
        return { root: payrollErrorMessage(error, t as PayrollErrorTranslator) };
    }
  }
  return { root: t("generic") };
}

type SalaryFieldTranslator = (
  key: "salaryDateTakenField" | "employeeNotFound" | "forbiddenSalary" | "generic",
) => string;

export type SalaryFieldName = "employee_id" | "amount" | "effective_from" | "root";
export type SalaryFieldErrors = Partial<Record<SalaryFieldName, string>>;

/** Map a stable salary error code onto the field that caused it. */
export function salaryFieldErrors(error: unknown, t: SalaryFieldTranslator): SalaryFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "salary_effective_date_taken":
        return { effective_from: t("salaryDateTakenField") };
      case "employee_not_found":
        return { employee_id: t("employeeNotFound") };
      case "forbidden":
        return { root: t("forbiddenSalary") };
      default:
        return { root: payrollErrorMessage(error, t as PayrollErrorTranslator) };
    }
  }
  return { root: t("generic") };
}

/** A failed run's `failed_code` becomes a recovery sentence, never internals. */
export function payrollRunFailureMessage(
  failedCode: string | null | undefined,
  t: (key: "failed" | "failedGeneric") => string,
): string {
  return failedCode === "payroll_calculation_failed" ? t("failed") : t("failedGeneric");
}
