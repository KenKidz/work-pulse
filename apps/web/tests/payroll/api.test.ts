/**
 * Cache-key coverage for self-service payslips (T11). Finalize must refresh
 * both the list key and a per-payslip detail key, which the generated client
 * shapes differently — the list key is a URL element plus params, while the
 * detail key is one URL string containing the id.
 */

import { describe, expect, it } from "vitest";

import { isPayrollPayslipQueryKey } from "@/features/payroll/api";

describe("isPayrollPayslipQueryKey", () => {
  it("matches the list and detail keys", () => {
    expect(isPayrollPayslipQueryKey(["/api/v1/payroll/payslips"])).toBe(true);
    expect(isPayrollPayslipQueryKey(["/api/v1/payroll/payslips", { page: 1 }])).toBe(true);
    expect(isPayrollPayslipQueryKey(["/api/v1/payroll/payslips/0190-payslip"])).toBe(true);
  });

  it("does not match unrelated or non-string roots", () => {
    expect(isPayrollPayslipQueryKey(["/api/v1/payroll/runs"])).toBe(false);
    expect(isPayrollPayslipQueryKey(["/api/v1/payroll/payslips-other"])).toBe(false);
    expect(isPayrollPayslipQueryKey([42])).toBe(false);
  });
});
