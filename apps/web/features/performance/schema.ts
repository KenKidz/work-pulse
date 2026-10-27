/**
 * Form schemas for the performance workspace (react-hook-form + zod). The
 * server remains the authority — these mirror the pinned contract in
 * `.ai/features/performance.md`: goal title 1..200, description up to 2,000, an
 * optional real calendar `due_date` (past dates are valid), progress 0..100;
 * review period up to 50, rating 1..5, strengths/improvements up to 2,000 and
 * at most 10 nominated providers; skill name up to 100, description/note up to
 * 500 and proficiency 1..5. Factories take the translated validation copy.
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
  | "progressRange"
  | "periodTooLong"
  | "ratingInvalid"
  | "strengthsTooLong"
  | "improvementsTooLong"
  | "maxProviders"
  | "commentRequired"
  | "commentTooLong"
  | "skillNameRequired"
  | "skillNameTooLong"
  | "skillDescriptionTooLong"
  | "skillRequired"
  | "proficiencyInvalid"
  | "noteTooLong";

type PerformanceValidation = (key: PerformanceValidationKey) => string;

/** The sentinel a Radix Select uses for "the actor's own employee record". */
export const SELF_EMPLOYEE = "self";

/** Select values stay strings in the form; submit converts them once. */
export const REVIEW_RATINGS = ["1", "2", "3", "4", "5"] as const;
export const PROFICIENCIES = ["1", "2", "3", "4", "5"] as const;
export const NO_RATING = "";
export const MAX_REVIEW_PROVIDERS = 10;

function isRating(value: string): boolean {
  return (REVIEW_RATINGS as readonly string[]).includes(value);
}

function isProficiency(value: string): boolean {
  return (PROFICIENCIES as readonly string[]).includes(value);
}

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

/**
 * One schema for create and edit. `employee_id` is hidden (but still present)
 * when editing and ignored on submit; `rating` is empty until the author picks
 * one, and only an edit sends the evaluative fields.
 */
export function reviewFormSchema(t: PerformanceValidation) {
  return z.object({
    employee_id: z.string().trim().uuid(t("employeeInvalid")),
    period: z.string().trim().max(50, t("periodTooLong")),
    rating: z
      .string()
      .trim()
      .refine((value) => value === NO_RATING || isRating(value), t("ratingInvalid")),
    strengths: z.string().trim().max(2000, t("strengthsTooLong")),
    improvements: z.string().trim().max(2000, t("improvementsTooLong")),
    feedback_provider_ids: z
      .array(z.string().uuid(t("employeeInvalid")))
      .max(MAX_REVIEW_PROVIDERS, t("maxProviders")),
  });
}

export type ReviewFormValues = z.infer<ReturnType<typeof reviewFormSchema>>;

export function feedbackFormSchema(t: PerformanceValidation) {
  return z.object({
    comment: z.string().trim().min(1, t("commentRequired")).max(2000, t("commentTooLong")),
  });
}

export type FeedbackFormValues = z.infer<ReturnType<typeof feedbackFormSchema>>;

export function skillFormSchema(t: PerformanceValidation) {
  return z.object({
    name: z.string().trim().min(1, t("skillNameRequired")).max(100, t("skillNameTooLong")),
    description: z.string().trim().max(500, t("skillDescriptionTooLong")),
  });
}

export type SkillFormValues = z.infer<ReturnType<typeof skillFormSchema>>;

/** Create attaches a skill; edit keeps the row's skill and only changes level/note. */
export function employeeSkillFormSchema(t: PerformanceValidation) {
  return z.object({
    skill_id: z.string().trim().uuid(t("skillRequired")),
    proficiency: z
      .string()
      .trim()
      .refine((value) => isProficiency(value), t("proficiencyInvalid")),
    note: z.string().trim().max(500, t("noteTooLong")),
  });
}

export type EmployeeSkillFormValues = z.infer<ReturnType<typeof employeeSkillFormSchema>>;
