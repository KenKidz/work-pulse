/**
 * Payroll error-code mapping (T10). Stable codes are pinned by the feature
 * spec; the UI must render a sentence for each of the six `409` lifecycle
 * codes and never echo a raw `payroll_*` code.
 */

import { describe, expect, it } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { bonusFieldErrors, runFieldErrors } from "@/features/payroll/hooks/use-payroll-runs";
import { deductionFieldErrors } from "@/features/payroll/hooks/use-payroll-deductions";
import { salaryFieldErrors } from "@/features/payroll/hooks/use-payroll-salaries";
import {
  isPayrollNotFoundError,
  PAYROLL_ERROR_MESSAGES,
  payrollErrorMessage,
  payrollRunFailureMessage,
} from "@/features/payroll/labels";

const LIFECYCLE_CODES = [
  "payroll_period_not_closed",
  "payroll_period_open",
  "payroll_run_not_calculated",
  "payroll_run_finalized",
  "payroll_employee_not_in_run",
  "salary_effective_date_taken",
] as const;

describe("payrollErrorMessage", () => {
  it.each(LIFECYCLE_CODES)("maps %s to a sentence without the raw code", (code) => {
    const message = payrollErrorMessage(new ApiError(409, code, "server text"));

    expect(message).toBe(PAYROLL_ERROR_MESSAGES[code]);
    expect(message).not.toContain(code);
    expect(message.length).toBeGreaterThan(0);
  });

  it("falls back to the server message for an unmapped code", () => {
    expect(payrollErrorMessage(new ApiError(400, "something_else", "A readable message"))).toBe(
      "A readable message",
    );
  });

  it("maps a forbidden response when the body carries no mapped code", () => {
    expect(payrollErrorMessage(new ApiError(403, null, "Forbidden"))).toBe(
      PAYROLL_ERROR_MESSAGES.forbidden,
    );
  });

  it("maps an unknown payroll id to a not-found sentence", () => {
    expect(payrollErrorMessage(new ApiError(404, null, "Not found"))).toBe(
      PAYROLL_ERROR_MESSAGES.payroll_not_found,
    );
  });

  it("returns the generic sentence for a non-API failure", () => {
    expect(payrollErrorMessage(new Error("boom"))).toBe("Something went wrong. Please try again.");
  });
});

describe("payroll error classification", () => {
  it("recognises only a 404 ApiError as not found", () => {
    expect(isPayrollNotFoundError(new ApiError(404, "payroll_run_not_found", "missing"))).toBe(
      true,
    );
    expect(isPayrollNotFoundError(new ApiError(403, "forbidden", "denied"))).toBe(false);
    expect(isPayrollNotFoundError(new Error("boom"))).toBe(false);
  });

  it("keeps a failed run's recovery copy free of the stable code", () => {
    const message = payrollRunFailureMessage("payroll_calculation_failed");
    expect(message).toContain("Recalculate");
    expect(message).not.toContain("payroll_calculation_failed");
    expect(payrollRunFailureMessage(null)).not.toContain("payroll_");
  });
});

describe("field-level error mapping", () => {
  it("puts create-run lifecycle errors on the period field", () => {
    expect(runFieldErrors(new ApiError(409, "payroll_period_not_closed", "not closed"))).toEqual({
      period: expect.stringContaining("closed"),
    });
    expect(runFieldErrors(new ApiError(409, "payroll_period_open", "open"))).toEqual({
      period: expect.stringContaining("open"),
    });
  });

  it("maps salary and deduction conflicts to the field that caused them", () => {
    expect(salaryFieldErrors(new ApiError(409, "salary_effective_date_taken", "taken"))).toEqual({
      effective_from: expect.stringContaining("effective"),
    });
    expect(
      deductionFieldErrors(new ApiError(409, "payroll_deduction_code_taken", "taken")),
    ).toEqual({ code: expect.stringContaining("code") });
  });

  it("maps a bonus for a non-member to the employee field", () => {
    expect(bonusFieldErrors(new ApiError(409, "payroll_employee_not_in_run", "missing"))).toEqual({
      employee_id: expect.stringContaining("part of this run"),
    });
  });
});
