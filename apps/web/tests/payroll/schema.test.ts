/**
 * Payroll form schemas (T10) — mirror the pinned API contract so the server is
 * not the first line of defense for obvious input errors.
 */

import { describe, expect, it } from "vitest";

import {
  bonusFormSchema,
  deductionFormSchema,
  payrollConfigFormSchema,
  runFormSchema,
  salaryFormSchema,
} from "@/features/payroll/schema";

const t = (key: string) => key;
const configLabels = {
  standardMonthlyHours: "standard monthly hours",
  overtimeMultiplier: "overtime multiplier",
};

describe("payrollConfigFormSchema", () => {
  it("accepts a three-letter currency and positive numbers", () => {
    const result = payrollConfigFormSchema(t, configLabels).safeParse({
      currency: "USD",
      standard_monthly_hours: "160",
      overtime_multiplier: "1.5",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.standard_monthly_hours).toBe(160);
      expect(result.data.overtime_multiplier).toBe(1.5);
    }
  });

  it("rejects a lowercase currency and zero values", () => {
    expect(
      payrollConfigFormSchema(t, configLabels).safeParse({
        currency: "usd",
        standard_monthly_hours: 160,
        overtime_multiplier: 1.5,
      }).success,
    ).toBe(false);
    expect(
      payrollConfigFormSchema(t, configLabels).safeParse({
        currency: "USD",
        standard_monthly_hours: 0,
        overtime_multiplier: 1.5,
      }).success,
    ).toBe(false);
    expect(
      payrollConfigFormSchema(t, configLabels).safeParse({
        currency: "USD",
        standard_monthly_hours: 160,
        overtime_multiplier: 0,
      }).success,
    ).toBe(false);
  });
});

describe("deductionFormSchema", () => {
  const base = {
    code: "TAX",
    name: "Withholding",
    active: true,
  };

  it("accepts percent-of-base within 0.01..100", () => {
    expect(
      deductionFormSchema(t).safeParse({
        ...base,
        kind: "percent_of_base",
        value: "10",
      }).success,
    ).toBe(true);
    expect(
      deductionFormSchema(t).safeParse({
        ...base,
        kind: "percent_of_base",
        value: "0.01",
      }).success,
    ).toBe(true);
    expect(
      deductionFormSchema(t).safeParse({
        ...base,
        kind: "percent_of_base",
        value: "100",
      }).success,
    ).toBe(true);
  });

  it("rejects percent-of-base outside the range", () => {
    expect(
      deductionFormSchema(t).safeParse({
        ...base,
        kind: "percent_of_base",
        value: "0",
      }).success,
    ).toBe(false);
    expect(
      deductionFormSchema(t).safeParse({
        ...base,
        kind: "percent_of_base",
        value: "100.01",
      }).success,
    ).toBe(false);
  });

  it("requires at least 0.01 for a fixed amount", () => {
    expect(
      deductionFormSchema(t).safeParse({
        ...base,
        kind: "fixed_amount",
        value: "0.005",
      }).success,
    ).toBe(false);
    expect(
      deductionFormSchema(t).safeParse({
        ...base,
        kind: "fixed_amount",
        value: "0.01",
      }).success,
    ).toBe(true);
  });
});

describe("salaryFormSchema", () => {
  const employee = "00000000-0000-4000-8000-0000000000e1";

  it("accepts a cent amount on a valid date", () => {
    expect(
      salaryFormSchema(t).safeParse({
        employee_id: employee,
        amount: "0.01",
        effective_from: "2026-01-01",
      }).success,
    ).toBe(true);
  });

  it("rejects a sub-cent amount, an invalid date and a malformed id", () => {
    expect(
      salaryFormSchema(t).safeParse({
        employee_id: employee,
        amount: "0.009",
        effective_from: "2026-01-01",
      }).success,
    ).toBe(false);
    expect(
      salaryFormSchema(t).safeParse({
        employee_id: employee,
        amount: "10",
        effective_from: "2026-13-01",
      }).success,
    ).toBe(false);
    expect(
      salaryFormSchema(t).safeParse({
        employee_id: "not-a-uuid",
        amount: "10",
        effective_from: "2026-01-01",
      }).success,
    ).toBe(false);
  });
});

describe("runFormSchema", () => {
  it("accepts a calendar month and rejects month 13", () => {
    expect(runFormSchema(t).safeParse({ period: "2026-09" }).success).toBe(true);
    expect(runFormSchema(t).safeParse({ period: "2026-13" }).success).toBe(false);
    expect(runFormSchema(t).safeParse({ period: "2026-9" }).success).toBe(false);
  });
});

describe("bonusFormSchema", () => {
  const employee = "00000000-0000-4000-8000-0000000000e1";

  it("caps the note at 200 characters", () => {
    expect(
      bonusFormSchema(t).safeParse({
        employee_id: employee,
        amount: "25",
        note: "a".repeat(200),
      }).success,
    ).toBe(true);
    expect(
      bonusFormSchema(t).safeParse({
        employee_id: employee,
        amount: "25",
        note: "a".repeat(201),
      }).success,
    ).toBe(false);
  });
});
