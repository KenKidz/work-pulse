/**
 * Form schemas for the payroll workspace (react-hook-form + zod). The server
 * remains the authority — these mirror the pinned API contract
 * (`.ai/features/payroll.md`): a 3-letter currency, positive config numbers,
 * cent amounts, `YYYY-MM-DD`/`YYYY-MM` dates and kind-dependent deduction
 * values. Factories take the translated validation copy.
 */

import { z } from "zod";

type PayrollValidation = (
  key:
    | "currencyInvalid"
    | "numberRequired"
    | "positive"
    | "amountMin"
    | "codeRequired"
    | "codeTooLong"
    | "nameRequired"
    | "nameTooLong"
    | "percentRange"
    | "employeeInvalid"
    | "dateInvalid"
    | "monthInvalid"
    | "noteTooLong",
  values?: Record<string, string | number>,
) => string;

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

export function payrollConfigFormSchema(t: PayrollValidation) {
  const currencyCode = z.string().regex(/^[A-Z]{3}$/, t("currencyInvalid"));
  const positiveNumber = (label: string) =>
    z.coerce
      .number({ message: t("numberRequired", { label }) })
      .gt(0, t("positive", { label }));

  return z.object({
    currency: currencyCode,
    standard_monthly_hours: positiveNumber("standard monthly hours"),
    overtime_multiplier: positiveNumber("overtime multiplier"),
  });
}

export type PayrollConfigFormValues = z.infer<ReturnType<typeof payrollConfigFormSchema>>;

export function deductionFormSchema(t: PayrollValidation) {
  const centAmount = z.coerce.number({ message: t("amountMin") }).min(0.01, t("amountMin"));

  return z
    .object({
      code: z.string().min(1, t("codeRequired")).max(50, t("codeTooLong")),
      name: z.string().min(1, t("nameRequired")).max(200, t("nameTooLong")),
      kind: z.enum(["percent_of_base", "fixed_amount"]),
      value: centAmount,
      active: z.boolean(),
    })
    .superRefine((values, context) => {
      if (values.kind === "percent_of_base" && values.value > 100) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["value"],
          message: t("percentRange"),
        });
      }
    });
}

export type DeductionFormValues = z.infer<ReturnType<typeof deductionFormSchema>>;

export function salaryFormSchema(t: PayrollValidation) {
  return z.object({
    employee_id: z.string().uuid(t("employeeInvalid")),
    amount: z.coerce.number({ message: t("amountMin") }).min(0.01, t("amountMin")),
    effective_from: z.string().refine(isCalendarDate, t("dateInvalid")),
  });
}

export type SalaryFormValues = z.infer<ReturnType<typeof salaryFormSchema>>;

export function runFormSchema(t: PayrollValidation) {
  return z.object({
    period: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/, t("monthInvalid")),
  });
}

export type RunFormValues = z.infer<ReturnType<typeof runFormSchema>>;

export function bonusFormSchema(t: PayrollValidation) {
  return z.object({
    employee_id: z.string().uuid(t("employeeInvalid")),
    amount: z.coerce.number({ message: t("amountMin") }).min(0.01, t("amountMin")),
    note: z.string().max(200, t("noteTooLong")).optional(),
  });
}

export type BonusFormValues = z.infer<ReturnType<typeof bonusFormSchema>>;
