/**
 * Form schemas for the payroll workspace (react-hook-form + zod). The server
 * remains the authority — these mirror the pinned API contract
 * (`.ai/features/payroll.md`): a 3-letter currency, positive config numbers,
 * cent amounts, `YYYY-MM-DD`/`YYYY-MM` dates and kind-dependent deduction
 * values.
 */

import { z } from "zod";

const currencyCode = z.string().regex(/^[A-Z]{3}$/, "Use a three-letter currency code, e.g. USD");

const positiveNumber = (label: string) =>
  z.coerce
    .number({ invalid_type_error: `Enter ${label}` })
    .gt(0, `${label[0]?.toUpperCase() ?? ""}${label.slice(1)} must be greater than zero`);

const centAmount = z.coerce
  .number({ invalid_type_error: "Enter an amount" })
  .min(0.01, "Amount must be at least 0.01");

/** `YYYY-MM-DD` that is also a real calendar day (no month 13 rollover). */
function isCalendarDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day
  );
}

const dateString = z.string().refine(isCalendarDate, "Choose a valid date");

export const payrollConfigFormSchema = z.object({
  currency: currencyCode,
  standard_monthly_hours: positiveNumber("standard monthly hours"),
  overtime_multiplier: positiveNumber("overtime multiplier"),
});

export type PayrollConfigFormValues = z.infer<typeof payrollConfigFormSchema>;

export const deductionFormSchema = z
  .object({
    code: z.string().min(1, "Code is required").max(50, "Code must be at most 50 characters"),
    name: z.string().min(1, "Name is required").max(200, "Name must be at most 200 characters"),
    kind: z.enum(["percent_of_base", "fixed_amount"]),
    value: centAmount,
    active: z.boolean(),
  })
  .superRefine((values, context) => {
    if (values.kind === "percent_of_base" && values.value > 100) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["value"],
        message: "Percent of base must be between 0.01 and 100",
      });
    }
  });

export type DeductionFormValues = z.infer<typeof deductionFormSchema>;

export const salaryFormSchema = z.object({
  employee_id: z.string().uuid("Choose a valid employee"),
  amount: centAmount,
  effective_from: dateString,
});

export type SalaryFormValues = z.infer<typeof salaryFormSchema>;

export const runFormSchema = z.object({
  period: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/, "Choose a month in YYYY-MM form"),
});

export type RunFormValues = z.infer<typeof runFormSchema>;

export const bonusFormSchema = z.object({
  employee_id: z.string().uuid("Choose an employee in this run"),
  amount: centAmount,
  note: z.string().max(200, "Note must be at most 200 characters").optional(),
});

export type BonusFormValues = z.infer<typeof bonusFormSchema>;
