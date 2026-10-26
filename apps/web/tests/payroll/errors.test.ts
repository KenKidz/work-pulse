/**
 * Payroll error-code mapping (T10). Stable codes are pinned by the feature
 * spec; the UI must render a sentence for each of the six `409` lifecycle
 * codes and never echo a raw `payroll_*` code.
 */

import { createTranslator } from "use-intl";
import { describe, expect, it } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { bonusFieldErrors, runFieldErrors } from "@/features/payroll/hooks/use-payroll-runs";
import { deductionFieldErrors } from "@/features/payroll/hooks/use-payroll-deductions";
import { salaryFieldErrors } from "@/features/payroll/hooks/use-payroll-salaries";
import { isPayrollNotFoundError } from "@/features/payroll/labels";
import { payrollErrorMessage, payrollRunFailureMessage } from "@/features/payroll/errors";
import messages from "@/messages/en";

const root = createTranslator({ locale: "en", messages, namespace: "payroll" });
const t = (key: string) => root(`errors.${key}` as never);
const runDetailT = (key: "failed" | "failedGeneric") => root(`runDetail.${key}`);

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
    const message = payrollErrorMessage(new ApiError(409, code, "server text"), t);

    expect(message).toBeTruthy();
    expect(message).not.toContain(code);
    expect(message.length).toBeGreaterThan(0);
  });

  it("falls back to the server message for an unmapped code", () => {
    expect(payrollErrorMessage(new ApiError(400, "something_else", "A readable message"), t)).toBe(
      "A readable message",
    );
  });

  it("maps a forbidden response when the body carries no mapped code", () => {
    expect(payrollErrorMessage(new ApiError(403, null, "Forbidden"), t)).toBe(
      t("forbidden"),
    );
  });

  it("maps an unknown payroll id to a not-found sentence", () => {
    expect(payrollErrorMessage(new ApiError(404, null, "Not found"), t)).toBe(
      t("payrollNotFound"),
    );
  });

  it("returns the generic sentence for a non-API failure", () => {
    expect(payrollErrorMessage(new Error("boom"), t)).toBe(t("generic"));
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
    const message = payrollRunFailureMessage("payroll_calculation_failed", runDetailT);
    expect(message).toContain("Recalculate");
    expect(message).not.toContain("payroll_calculation_failed");
    expect(payrollRunFailureMessage(null, runDetailT)).not.toContain("payroll_");
  });
});

describe("field-level error mapping", () => {
  it("puts create-run lifecycle errors on the period field", () => {
    expect(runFieldErrors(new ApiError(409, "payroll_period_not_closed", "not closed"), t)).toEqual({
      period: expect.stringContaining("closed"),
    });
    expect(runFieldErrors(new ApiError(409, "payroll_period_open", "open"), t)).toEqual({
      period: expect.stringContaining("open"),
    });
  });

  it("maps salary and deduction conflicts to the field that caused them", () => {
    expect(salaryFieldErrors(new ApiError(409, "salary_effective_date_taken", "taken"), t)).toEqual({
      effective_from: expect.stringContaining("effective"),
    });
    expect(
      deductionFieldErrors(new ApiError(409, "payroll_deduction_code_taken", "taken"), t),
    ).toEqual({ code: expect.stringContaining("code") });
  });

  it("maps a bonus for a non-member to the employee field", () => {
    expect(bonusFieldErrors(new ApiError(409, "payroll_employee_not_in_run", "missing"), t)).toEqual({
      employee_id: expect.stringContaining("part of this run"),
    });
  });
});
