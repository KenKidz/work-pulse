/**
 * Form schemas for the performance workspace (react-hook-form + zod). The
 * server remains the authority — these mirror the pinned contract in
 * `.ai/features/performance.md`: title 1..200, description up to 2,000, an
 * optional real calendar `due_date` (past dates are valid), and progress 0..100.
 * Factories take the translated validation copy.
 */

import { z } from "zod";

import { isCalendarDate } from "./dates";

export type PerformanceValidationKey =
  | "titleRequired"
  | "titleTooLong"
  | "descriptionTooLong"
  | "employeeInvalid"
  | "dueDateInvalid"
  | "progressRequired"
  | "progressInt"
  | "progressRange";

type PerformanceValidation = (key: PerformanceValidationKey) => string;

/** The sentinel a Radix Select uses for "the actor's own employee record". */
export const SELF_EMPLOYEE = "self";

export function goalFormSchema(t: PerformanceValidation) {
  return z.object({
    employee_id: z
      .string()
      .trim()
      .refine(
        (value) => value === SELF_EMPLOYEE || z.string().uuid().safeParse(value).success,
        t("employeeInvalid"),
      ),
    title: z.string().trim().min(1, t("titleRequired")).max(200, t("titleTooLong")),
    description: z.string().trim().max(2000, t("descriptionTooLong")),
    due_date: z
      .string()
      .trim()
      .refine(
        (value) => value === "" || (/^\d{4}-\d{2}-\d{2}$/.test(value) && isCalendarDate(value)),
        t("dueDateInvalid"),
      ),
    // A cleared number input arrives as ""; like the job openings form, the
    // schema coerces it to 0 — the pinned default for a new goal.
    progress_pct: z.coerce
      .number({ message: t("progressRequired") })
      .int(t("progressInt"))
      .min(0, t("progressRange"))
      .max(100, t("progressRange")),
  });
}

export type GoalFormValues = z.infer<ReturnType<typeof goalFormSchema>>;
