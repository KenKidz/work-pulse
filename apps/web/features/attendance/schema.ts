/**
 * Form schemas for the attendance feature (react-hook-form + zod).
 * The server stays the authority — these mirror the pinned API contract
 * (`.ai/features/attendance.md`): server time only (Decision #3), so no
 * timestamp field exists anywhere in the forms. Notes are 1..500 when present.
 */

import { z } from "zod";

export const checkInFormSchema = z.object({
  work_mode: z.enum(["onsite", "remote"]),
  check_in_note: z
    .string()
    .min(1, "Note must be at least 1 character")
    .max(500, "Note must be at most 500 characters")
    .nullish(),
});

export type CheckInFormValues = z.infer<typeof checkInFormSchema>;

export const checkOutFormSchema = z.object({
  check_out_note: z
    .string()
    .min(1, "Note must be at least 1 character")
    .max(500, "Note must be at most 500 characters")
    .nullish(),
});

export type CheckOutFormValues = z.infer<typeof checkOutFormSchema>;

export const shiftFormSchema = z.object({
  name: z.string().min(1, "Name is required").max(200, "Name must be at most 200 characters"),
  start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Start time must be HH:MM"),
  end_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "End time must be HH:MM"),
  grace_minutes: z.coerce
    .number()
    .int("Grace must be a whole number of minutes")
    .min(0, "Grace cannot be negative"),
});

export type ShiftFormValues = z.infer<typeof shiftFormSchema>;
