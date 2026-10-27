/**
 * Status tones, stable-code mapping and label helpers for performance.
 * Human-facing labels live in the `performance` message namespace; wire values
 * never reach the DOM (workpulse-ui §6).
 */

import type { ComponentProps } from "react";

import type { Badge } from "@/components/ui/badge";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import type { GoalStatus, ReviewStatus } from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

export const goalStatusVariant: Record<GoalStatus, BadgeVariant> = {
  draft: "outline",
  active: "secondary",
  completed: "success",
  cancelled: "muted",
};

export const reviewStatusVariant: Record<ReviewStatus, BadgeVariant> = {
  draft: "outline",
  completed: "success",
  cancelled: "muted",
};

export type PerformanceErrorKey =
  | "goalNotFound"
  | "goalNotDraft"
  | "goalNotActive"
  | "goalTerminal"
  | "reviewNotFound"
  | "reviewIncomplete"
  | "reviewNotDraft"
  | "feedbackProviderInvalid"
  | "feedbackAlreadySubmitted"
  | "feedbackAccessDenied"
  | "skillNameTaken"
  | "skillArchived"
  | "employeeSkillExists"
  | "employeeNotActive"
  | "employeeNotFound"
  | "skillNotFound"
  | "denied"
  | "validation"
  | "forbidden"
  | "notFound"
  | "generic";

/** A stable, user-facing sentence for a performance failure (never a raw code). */
export function performanceErrorMessage(
  error: unknown,
  t: (key: PerformanceErrorKey) => string,
): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "goal_not_found":
        return t("goalNotFound");
      case "goal_not_draft":
        return t("goalNotDraft");
      case "goal_not_active":
        return t("goalNotActive");
      case "goal_terminal":
        return t("goalTerminal");
      case "review_not_found":
        return t("reviewNotFound");
      case "review_incomplete":
        return t("reviewIncomplete");
      case "review_not_draft":
        return t("reviewNotDraft");
      case "feedback_provider_invalid":
        return t("feedbackProviderInvalid");
      case "feedback_already_submitted":
        return t("feedbackAlreadySubmitted");
      case "feedback_access_denied":
        return t("feedbackAccessDenied");
      case "skill_name_taken":
        return t("skillNameTaken");
      case "skill_archived":
        return t("skillArchived");
      case "employee_skill_exists":
        return t("employeeSkillExists");
      case "employee_not_active":
        return t("employeeNotActive");
      case "employee_not_found":
        return t("employeeNotFound");
      case "skill_not_found":
        return t("skillNotFound");
      case "performance_denied":
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
  return (
    error instanceof ApiError &&
    (error.status === 403 ||
      error.code === "performance_denied" ||
      error.code === "feedback_access_denied")
  );
}

/**
 * A joined employee name from the directory, else the caller's localized
 * id-tail fallback (`Employee …a1b2`).
 */
export function employeeLabel(
  names: ReadonlyMap<string, string>,
  id: string,
  fallback: string,
): string {
  const name = names.get(id);
  return name !== undefined && name.length > 0 ? name : fallback;
}
