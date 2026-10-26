/**
 * Form schemas for the overtime feature (react-hook-form + zod). The server is
 * the authority; these mirror the pinned input shapes: a real calendar
 * `YYYY-MM-DD` work date, hours `0 < hours ≤ 12` with at most two decimals,
 * and reason/decision notes of at most 500 characters.
 */

import { z } from "zod";

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

type OvertimeRequestValidation = (
  key:
    | "dateInvalid"
    | "hoursRequired"
    | "hoursDecimals"
    | "hoursPositive"
    | "hoursMax"
    | "reasonTooLong",
) => string;

export function overtimeRequestFormSchema(t: OvertimeRequestValidation) {
  const dateString = z.string().refine(isCalendarDate, t("dateInvalid"));

  /** Hours stay a string in the form so the two-decimal rule is checked on input. */
  const hoursString = z
    .string()
    .min(1, t("hoursRequired"))
    .regex(/^\d{1,2}(\.\d{1,2})?$/, t("hoursDecimals"))
    .refine((value) => Number(value) > 0, t("hoursPositive"))
    .refine((value) => Number(value) <= 12, t("hoursMax"));

  return z.object({
    work_date: dateString,
    hours: hoursString,
    reason: z.string().max(500, t("reasonTooLong")).optional(),
  });
}

export type OvertimeRequestFormValues = z.infer<ReturnType<typeof overtimeRequestFormSchema>>;

type OvertimeDecisionValidation = (key: "noteTooLong") => string;

export function overtimeDecisionFormSchema(t: OvertimeDecisionValidation) {
  return z.object({
    note: z.string().max(500, t("noteTooLong")).optional(),
  });
}

export type OvertimeDecisionFormValues = z.infer<
  ReturnType<typeof overtimeDecisionFormSchema>
>;

