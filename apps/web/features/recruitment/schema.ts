/**
 * Form schemas for the recruitment workspace (react-hook-form + zod). The
 * server remains the authority — these mirror the pinned contract in
 * `.ai/features/recruitment.md`: title 1..200, description up to 5000,
 * organization references are uuids, and a job has at least one opening.
 *
 * Candidate intake bounds mirror `CandidateCreate` (names 1..200, email
 * 3..320, notes up to 2000) and the hire form mirrors `HireCandidate` (an
 * optional override defaults to the candidate record; `hired_at` is the
 * server's UTC date at the latest).
 */

import { z } from "zod";

import { dayjs } from "@/lib/datetime";

import { todayISO } from "./dates";

const organizationRef = (label: string) => z.string().uuid(`Choose a ${label}`);

/** A real calendar date, not just the `YYYY-MM-DD` shape (dayjs rolls over). */
function isCalendarDate(value: string): boolean {
  const parsed = dayjs.utc(value);
  return parsed.isValid() && parsed.format("YYYY-MM-DD") === value;
}

export const jobFormSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "Enter the job title")
    .max(200, "Title must be at most 200 characters"),
  description: z.string().max(5000, "Description must be at most 5,000 characters"),
  department_id: organizationRef("department"),
  position_id: organizationRef("position"),
  location_id: organizationRef("location"),
  openings: z.coerce
    .number({ invalid_type_error: "Enter the number of openings" })
    .int("Openings must be a whole number")
    .min(1, "A job needs at least one opening"),
});

export type JobFormValues = z.infer<typeof jobFormSchema>;

const candidateSource = z.enum(["referral", "job_board", "agency", "other"]);

/** Contact fields shared by intake (create) and edit (update). */
export const candidateFormSchema = z.object({
  first_name: z
    .string()
    .trim()
    .min(1, "Enter the first name")
    .max(200, "First name must be at most 200 characters"),
  last_name: z
    .string()
    .trim()
    .min(1, "Enter the last name")
    .max(200, "Last name must be at most 200 characters"),
  email: z
    .string()
    .trim()
    .min(3, "Enter an email address")
    .max(320, "Email must be at most 320 characters")
    .email("Enter a valid email address"),
  phone: z.string().trim().max(50, "Phone must be at most 50 characters"),
  source: candidateSource,
  notes: z.string().trim().max(2000, "Notes must be at most 2,000 characters"),
});

/** Intake also pins the open job the application belongs to. */
export const candidateCreateSchema = candidateFormSchema.extend({
  job_id: z.string().uuid("Choose a job"),
});

export type CandidateCreateValues = z.infer<typeof candidateCreateSchema>;

/** Reject/withdraw note — 1..500 characters when present. */
export const candidateDecisionSchema = z.object({
  note: z.string().trim().max(500, "Note must be at most 500 characters"),
});

export type CandidateDecisionValues = z.infer<typeof candidateDecisionSchema>;

/** Optional override: blank means "use the candidate's recorded email". */
const optionalEmail = z
  .string()
  .trim()
  .max(320, "Email must be at most 320 characters")
  .refine(
    (value) => value === "" || z.string().email().safeParse(value).success,
    "Enter a valid email address",
  );

/**
 * Hire terms. Contact overrides start blank and fall back to the candidate
 * record; department/position/location always come from the job (server).
 */
export const hireFormSchema = z.object({
  employment_type: z.enum(["full_time", "part_time", "contract", "intern"]),
  hired_at: z
    .string()
    .min(1, "Choose the hire date")
    .regex(/^\d{4}-\d{2}-\d{2}$/, "Choose a valid hire date")
    .refine(isCalendarDate, "Choose a valid hire date")
    .refine((value) => value <= todayISO(), "Hire date can't be in the future"),
  manager_id: z.string(),
  employee_code: z.string().trim().max(50, "Employee code must be at most 50 characters"),
  first_name: z.string().trim().max(200, "First name must be at most 200 characters"),
  last_name: z.string().trim().max(200, "Last name must be at most 200 characters"),
  email: optionalEmail,
  phone: z.string().trim().max(50, "Phone must be at most 50 characters"),
});

export type HireFormValues = z.infer<typeof hireFormSchema>;
