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

const dateString = z.string().refine(isCalendarDate, "Choose a valid date");

/** Hours stay a string in the form so the two-decimal rule is checked on input. */
const hoursString = z
  .string()
  .min(1, "Enter the hours worked")
  .regex(/^\d{1,2}(\.\d{1,2})?$/, "Use hours with at most two decimals")
  .refine((value) => Number(value) > 0, "Hours must be greater than zero")
  .refine((value) => Number(value) <= 12, "Hours must be at most 12");

const reasonField = z.string().max(500, "Reason must be at most 500 characters").optional();

export const overtimeRequestFormSchema = z.object({
  work_date: dateString,
  hours: hoursString,
  reason: reasonField,
});

export type OvertimeRequestFormValues = z.infer<typeof overtimeRequestFormSchema>;

export const overtimeDecisionFormSchema = z.object({
  note: z.string().max(500, "Note must be at most 500 characters").optional(),
});

export type OvertimeDecisionFormValues = z.infer<typeof overtimeDecisionFormSchema>;
