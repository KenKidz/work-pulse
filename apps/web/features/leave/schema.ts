/**
 * Form schemas for the leave feature (react-hook-form + zod). The server is
 * the authority (`.ai/features/leave.md`); these mirror the pinned input
 * shapes: whole calendar days, reason 1..500 when present, optional decision
 * note 1..500.
 */

import { z } from "zod";

const dateString = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Choose a valid date");

export const leaveRequestFormSchema = z
  .object({
    leave_type_id: z.string().min(1, "Select a leave type"),
    start_date: dateString,
    end_date: dateString,
    reason: z.string().max(500, "Reason must be at most 500 characters").optional(),
  })
  .refine((values) => values.end_date >= values.start_date, {
    path: ["end_date"],
    message: "End date must be on or after the start date",
  });

export type LeaveRequestFormValues = z.infer<typeof leaveRequestFormSchema>;

export const leaveDecisionFormSchema = z.object({
  decision_note: z.string().max(500, "Note must be at most 500 characters").optional(),
});

export type LeaveDecisionFormValues = z.infer<typeof leaveDecisionFormSchema>;
