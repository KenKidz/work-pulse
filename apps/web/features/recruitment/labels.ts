/**
 * Human-facing labels, status tones and pinned error messages for recruitment
 * — wire values and stable codes are never rendered (workpulse-ui §6).
 */

import type { ComponentProps } from "react";

import type { Badge } from "@/components/ui/badge";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import type { CandidateSource, CandidateStatus, InterviewStatus, JobStatus } from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

export const jobStatusLabel: Record<JobStatus, string> = {
  draft: "Draft",
  open: "Open",
  closed: "Closed",
};

export const jobStatusVariant: Record<JobStatus, BadgeVariant> = {
  draft: "secondary",
  open: "success",
  closed: "muted",
};

export const candidateStatusLabel: Record<CandidateStatus, string> = {
  applied: "Applied",
  interviewing: "Interviewing",
  offered: "Offered",
  hired: "Hired",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export const candidateStatusVariant: Record<CandidateStatus, BadgeVariant> = {
  applied: "outline",
  interviewing: "secondary",
  offered: "warning",
  hired: "success",
  rejected: "destructive",
  withdrawn: "muted",
};

export const candidateSourceLabel: Record<CandidateSource, string> = {
  referral: "Referral",
  job_board: "Job board",
  agency: "Agency",
  other: "Other",
};

export const interviewStatusLabel: Record<InterviewStatus, string> = {
  scheduled: "Scheduled",
  completed: "Completed",
  cancelled: "Cancelled",
};

export const interviewStatusVariant: Record<InterviewStatus, BadgeVariant> = {
  scheduled: "warning",
  completed: "success",
  cancelled: "muted",
};

/**
 * Stable code → sentence. Every pinned job/candidate/interview code, plus the
 * Employees and Organization codes the hire seam and job references propagate
 * unchanged, has an entry so no raw code reaches a toast or alert.
 */
export const RECRUITMENT_ERROR_MESSAGES: Record<string, string> = {
  job_not_found: "This job is no longer available.",
  candidate_not_found: "This candidate is no longer available.",
  interview_not_found: "This interview is no longer available.",
  interviewer_not_found: "Choose an employee from your organization as the interviewer.",
  job_not_draft: "Only a draft job can be opened. Refresh and try again.",
  job_not_open: "This job's state no longer allows that action. Refresh the list and try again.",
  job_openings_filled:
    "Every opening on this job is filled. Increase the openings before hiring again.",
  candidate_already_applied: "This candidate has already applied to this job.",
  candidate_stage_conflict:
    "The candidate isn't at the stage this action needs. Refresh and try again.",
  candidate_not_open: "This candidate is already closed and can't be rejected or withdrawn again.",
  interview_not_scheduled: "This interview is no longer scheduled. Refresh and try again.",
  interviewer_not_active: "That employee is no longer active and can't be assigned.",
  employee_email_taken:
    "An employee already uses this email. Override the email or update the existing record.",
  employee_code_taken: "This employee code is already in use.",
  employee_manager_invalid: "Choose an active manager inside your organization.",
  department_not_found: "The selected department no longer exists. Pick another one.",
  position_not_found: "The selected position no longer exists. Pick another one.",
  location_not_found: "The selected location no longer exists. Pick another one.",
  recruitment_denied: "You don't have permission to manage recruitment.",
  validation_error: "Check the highlighted fields and try again.",
  forbidden: "You don't have permission to do that.",
};

/** A stable, user-facing sentence for a recruitment failure (never a raw code). */
export function recruitmentErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const mapped = error.code === null ? undefined : RECRUITMENT_ERROR_MESSAGES[error.code];
    if (mapped !== undefined) return mapped;
    if (error.status === 403) return RECRUITMENT_ERROR_MESSAGES.forbidden ?? "Forbidden.";
    if (error.status === 404) return "This item is no longer available.";
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

/** Shared role-denied classification for read and mutation surfaces. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "recruitment_denied");
}

/**
 * A joined organization name from the directory, else the app-wide id-tail
 * convention (`Department …a1b2`) — the employees/users fallback.
 */
export function organizationLabel(
  names: ReadonlyMap<string, string>,
  id: string,
  kind: string,
): string {
  const name = names.get(id);
  return name !== undefined && name.length > 0 ? name : `${kind} …${id.slice(-4)}`;
}
