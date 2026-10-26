/**
 * Form schemas for the leave feature (react-hook-form + zod). The server is
 * the authority (`.ai/features/leave.md`); these mirror the pinned input
 * shapes: whole calendar days, reason 1..500 when present, optional decision
 * note 1..500.
 */

import { z } from "zod";

type LeaveRequestValidation = (
  key: "dateInvalid" | "typeRequired" | "reasonTooLong" | "endBeforeStart",
) => string;

export function leaveRequestFormSchema(t: LeaveRequestValidation) {
  const dateString = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, t("dateInvalid"));

  return z
    .object({
      leave_type_id: z.string().min(1, t("typeRequired")),
      start_date: dateString,
      end_date: dateString,
      reason: z.string().max(500, t("reasonTooLong")).optional(),
    })
    .refine((values) => values.end_date >= values.start_date, {
      path: ["end_date"],
      message: t("endBeforeStart"),
    });
}

export type LeaveRequestFormValues = z.infer<ReturnType<typeof leaveRequestFormSchema>>;

type LeaveDecisionValidation = (key: "noteTooLong") => string;

export function leaveDecisionFormSchema(t: LeaveDecisionValidation) {
  return z.object({
    decision_note: z.string().max(500, t("noteTooLong")).optional(),
  });
}

export type LeaveDecisionFormValues = z.infer<ReturnType<typeof leaveDecisionFormSchema>>;
