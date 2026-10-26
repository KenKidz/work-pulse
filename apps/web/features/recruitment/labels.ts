/**
 * Status tones, pipeline classifiers and stable-code mapping for recruitment.
 * Human-facing labels and sentences live in the `recruitment` message
 * namespace; wire values never reach the DOM (workpulse-ui §6).
 */

import type { ComponentProps } from "react";

import type { Badge } from "@/components/ui/badge";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import type {
  CandidateStatus,
  InterviewStatus,
  JobStatus,
} from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

export const jobStatusVariant: Record<JobStatus, BadgeVariant> = {
  draft: "secondary",
  open: "success",
  closed: "muted",
};

export const candidateStatusVariant: Record<CandidateStatus, BadgeVariant> = {
  applied: "outline",
  interviewing: "secondary",
  offered: "warning",
  hired: "success",
  rejected: "destructive",
  withdrawn: "muted",
};

/** A candidate can still be moved only from these stages. */
export const CANDIDATE_OPEN_STATUSES: readonly CandidateStatus[] = [
  "applied",
  "interviewing",
  "offered",
];

export function isCandidateOpen(status: CandidateStatus): boolean {
  return CANDIDATE_OPEN_STATUSES.includes(status);
}

/** The one stage an `advance` moves to, or `null` when the pipeline is done. */
export function nextCandidateStage(status: CandidateStatus): CandidateStatus | null {
  if (status === "applied") return "interviewing";
  if (status === "interviewing") return "offered";
  return null;
}

export const interviewStatusVariant: Record<InterviewStatus, BadgeVariant> = {
  scheduled: "warning",
  completed: "success",
  cancelled: "muted",
};

export type RecruitmentErrorKey =
  | "jobNotFound"
  | "candidateNotFound"
  | "interviewNotFound"
  | "interviewerNotFound"
  | "jobNotDraft"
  | "jobNotOpen"
  | "jobOpeningsFilled"
  | "candidateAlreadyApplied"
  | "candidateStageConflict"
  | "candidateNotOpen"
  | "interviewNotScheduled"
  | "interviewerNotActive"
  | "employeeEmailTaken"
  | "employeeCodeTaken"
  | "employeeManagerInvalid"
  | "departmentNotFound"
  | "positionNotFound"
  | "locationNotFound"
  | "denied"
  | "validation"
  | "forbidden"
  | "notFound"
  | "generic";

/** A stable, user-facing sentence for a recruitment failure (never a raw code). */
export function recruitmentErrorMessage(
  error: unknown,
  t: (key: RecruitmentErrorKey) => string,
): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "job_not_found":
        return t("jobNotFound");
      case "candidate_not_found":
        return t("candidateNotFound");
      case "interview_not_found":
        return t("interviewNotFound");
      case "interviewer_not_found":
        return t("interviewerNotFound");
      case "job_not_draft":
        return t("jobNotDraft");
      case "job_not_open":
        return t("jobNotOpen");
      case "job_openings_filled":
        return t("jobOpeningsFilled");
      case "candidate_already_applied":
        return t("candidateAlreadyApplied");
      case "candidate_stage_conflict":
        return t("candidateStageConflict");
      case "candidate_not_open":
        return t("candidateNotOpen");
      case "interview_not_scheduled":
        return t("interviewNotScheduled");
      case "interviewer_not_active":
        return t("interviewerNotActive");
      case "employee_email_taken":
        return t("employeeEmailTaken");
      case "employee_code_taken":
        return t("employeeCodeTaken");
      case "employee_manager_invalid":
        return t("employeeManagerInvalid");
      case "department_not_found":
        return t("departmentNotFound");
      case "position_not_found":
        return t("positionNotFound");
      case "location_not_found":
        return t("locationNotFound");
      case "recruitment_denied":
        return t("denied");
      case "validation_error":
        return t("validation");
      case "forbidden":
        return t("forbidden");
      default:
        if (error.status === 403) return t("forbidden");
        if (error.status === 404) return t("notFound");
        return error.message;
    }
  }
  return t("generic");
}

/** Shared role-denied classification for read and mutation surfaces. */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "recruitment_denied");
}

/**
 * A joined organization name from the directory, else the caller's localized
 * id-tail fallback (`Department …a1b2`).
 */
export function organizationLabel(
  names: ReadonlyMap<string, string>,
  id: string,
  fallback: string,
): string {
  const name = names.get(id);
  return name !== undefined && name.length > 0 ? name : fallback;
}
