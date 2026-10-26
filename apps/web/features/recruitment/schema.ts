/**
 * Form schemas for the recruitment workspace (react-hook-form + zod). The
 * server remains the authority — these mirror the pinned contract in
 * `.ai/features/recruitment.md`: title 1..200, description up to 5000,
 * organization references are uuids, and a job has at least one opening.
 * Factories take the translated validation copy.
 */

import { z } from "zod";

import { dayjs } from "@/lib/datetime";

import { todayISO } from "./dates";

export type RecruitmentValidationKey =
  | "titleRequired"
  | "titleTooLong"
  | "descriptionTooLong"
  | "openingsRequired"
  | "openingsInt"
  | "openingsMin"
  | "firstNameRequired"
  | "firstNameTooLong"
  | "lastNameRequired"
  | "lastNameTooLong"
  | "emailRequired"
  | "emailTooLong"
  | "emailInvalid"
  | "phoneTooLong"
  | "notesTooLong"
  | "jobRequired"
  | "departmentRequired"
  | "positionRequired"
  | "locationRequired"
  | "noteTooLong"
  | "hireDateRequired"
  | "hireDateInvalid"
  | "hireDateFuture"
  | "employeeCodeTooLong"
  | "candidateRequired"
  | "interviewerRequired"
  | "dateTimeRequired"
  | "dateTimeInvalid"
  | "interviewNotesTooLong"
  | "ratingRequired"
  | "feedbackTooLong";

type RecruitmentValidation = (key: RecruitmentValidationKey) => string;

/** A real calendar date, not just the `YYYY-MM-DD` shape (dayjs rolls over). */
function isCalendarDate(value: string): boolean {
  const parsed = dayjs.utc(value);
  return parsed.isValid() && parsed.format("YYYY-MM-DD") === value;
}

export function jobFormSchema(t: RecruitmentValidation) {
  const organizationRef = (label: string) => z.string().uuid(label);

  return z.object({
    title: z.string().trim().min(1, t("titleRequired")).max(200, t("titleTooLong")),
    description: z.string().max(5000, t("descriptionTooLong")),
    department_id: organizationRef(t("departmentRequired")),
    position_id: organizationRef(t("positionRequired")),
    location_id: organizationRef(t("locationRequired")),
    openings: z.coerce
      .number({ message: t("openingsRequired") })
      .int(t("openingsInt"))
      .min(1, t("openingsMin")),
  });
}

export type JobFormValues = z.infer<ReturnType<typeof jobFormSchema>>;

const candidateSource = z.enum(["referral", "job_board", "agency", "other"]);

/** Contact fields shared by intake (create) and edit (update). */
export function candidateFormSchema(t: RecruitmentValidation) {
  return z.object({
    first_name: z.string().trim().min(1, t("firstNameRequired")).max(200, t("firstNameTooLong")),
    last_name: z.string().trim().min(1, t("lastNameRequired")).max(200, t("lastNameTooLong")),
    email: z
      .string()
      .trim()
      .min(3, t("emailRequired"))
      .max(320, t("emailTooLong"))
      .email(t("emailInvalid")),
    phone: z.string().trim().max(50, t("phoneTooLong")),
    source: candidateSource,
    notes: z.string().trim().max(2000, t("notesTooLong")),
  });
}

/** Intake also pins the open job the application belongs to. */
export function candidateCreateSchema(t: RecruitmentValidation) {
  return candidateFormSchema(t).extend({
    job_id: z.string().uuid(t("jobRequired")),
  });
}

export type CandidateCreateValues = z.infer<ReturnType<typeof candidateCreateSchema>>;

/** Reject/withdraw note — up to 500 characters when present. */
export function candidateDecisionSchema(t: RecruitmentValidation) {
  return z.object({
    note: z.string().trim().max(500, t("noteTooLong")),
  });
}

export type CandidateDecisionValues = z.infer<ReturnType<typeof candidateDecisionSchema>>;

/**
 * Hire terms. Contact overrides start blank and fall back to the candidate
 * record; department/position/location always come from the job (server).
 */
export function hireFormSchema(t: RecruitmentValidation) {
  const optionalEmail = z
    .string()
    .trim()
    .max(320, t("emailTooLong"))
    .refine(
      (value) => value === "" || z.string().email().safeParse(value).success,
      t("emailInvalid"),
    );

  return z.object({
    employment_type: z.enum(["full_time", "part_time", "contract", "intern"]),
    hired_at: z
      .string()
      .min(1, t("hireDateRequired"))
      .regex(/^\d{4}-\d{2}-\d{2}$/, t("hireDateInvalid"))
      .refine(isCalendarDate, t("hireDateInvalid"))
      .refine((value) => value <= todayISO(), t("hireDateFuture")),
    manager_id: z.string(),
    employee_code: z.string().trim().max(50, t("employeeCodeTooLong")),
    first_name: z.string().trim().max(200, t("firstNameTooLong")),
    last_name: z.string().trim().max(200, t("lastNameTooLong")),
    email: optionalEmail,
    phone: z.string().trim().max(50, t("phoneTooLong")),
  });
}

export type HireFormValues = z.infer<ReturnType<typeof hireFormSchema>>;

/** Scheduling mirrors `InterviewSchedule`: interviewer, time, optional notes. */
export function interviewScheduleSchema(t: RecruitmentValidation) {
  const dateTimeInput = z
    .string()
    .min(1, t("dateTimeRequired"))
    .refine((value) => dayjs(value).isValid(), t("dateTimeInvalid"));

  return z.object({
    candidate_id: z.string().uuid(t("candidateRequired")),
    interviewer_id: z.string().uuid(t("interviewerRequired")),
    scheduled_at: dateTimeInput,
    notes: z.string().trim().max(1000, t("interviewNotesTooLong")),
  });
}

export type InterviewScheduleValues = z.infer<ReturnType<typeof interviewScheduleSchema>>;

/** The pinned feedback scale, as the strings a form control produces. */
export const INTERVIEW_RATINGS = ["1", "2", "3", "4", "5"] as const;

/** Feedback: the rating is an explicit 1..5 choice; notes are optional. */
export function interviewFeedbackSchema(t: RecruitmentValidation) {
  return z.object({
    rating: z
      .string()
      .min(1, t("ratingRequired"))
      .refine(
        (value) => (INTERVIEW_RATINGS as readonly string[]).includes(value),
        t("ratingRequired"),
      ),
    notes: z.string().trim().max(1000, t("feedbackTooLong")),
  });
}

export type InterviewFeedbackValues = z.infer<ReturnType<typeof interviewFeedbackSchema>>;
