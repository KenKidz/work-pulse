/**
 * Form schemas for the attendance feature (react-hook-form + zod).
 * The server stays the authority — these mirror the pinned API contract
 * (`.ai/features/attendance.md`): server time only (Decision #3), so no
 * timestamp field exists anywhere in the forms. Notes are 1..500 when present.
 */

import { z } from "zod";

type NoteValidation = (key: "noteMin" | "noteMax") => string;

export function checkInFormSchema(t: NoteValidation) {
  return z.object({
    work_mode: z.enum(["onsite", "remote"]),
    check_in_note: z.string().min(1, t("noteMin")).max(500, t("noteMax")).nullish(),
  });
}

export type CheckInFormValues = z.infer<ReturnType<typeof checkInFormSchema>>;

export function checkOutFormSchema(t: NoteValidation) {
  return z.object({
    check_out_note: z.string().min(1, t("noteMin")).max(500, t("noteMax")).nullish(),
  });
}

export type CheckOutFormValues = z.infer<ReturnType<typeof checkOutFormSchema>>;

type ShiftValidation = (
  key:
    | "nameRequired"
    | "nameTooLong"
    | "startTimeFormat"
    | "endTimeFormat"
    | "graceInteger"
    | "graceNonNegative",
) => string;

export function shiftFormSchema(t: ShiftValidation) {
  return z.object({
    name: z.string().min(1, t("nameRequired")).max(200, t("nameTooLong")),
    start_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, t("startTimeFormat")),
    end_time: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, t("endTimeFormat")),
    grace_minutes: z.coerce.number().int(t("graceInteger")).min(0, t("graceNonNegative")),
  });
}

export type ShiftFormValues = z.infer<ReturnType<typeof shiftFormSchema>>;
