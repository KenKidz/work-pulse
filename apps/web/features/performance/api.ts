/**
 * Query-cache keys and invalidation helpers for the performance feature.
 *
 * The generated client keys queries by URL + params, so invalidating with the
 * parameterless base key refreshes every variation — a status filter, a
 * different employee and the subject's own list all settle together after a
 * review transition. Employee-skill rows are keyed by employee id, so catalog
 * renames invalidate the whole `/employees` prefix while a single row change
 * invalidates only that employee.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getListEmployeeSkillsQueryKey,
  getListGoalsQueryKey,
  getListReviewFeedbackQueryKey,
  getListReviewsQueryKey,
  getListSkillsQueryKey,
} from "@workpulse/api-client";

export const performanceGoalsQueryKey = [...getListGoalsQueryKey()];
export const performanceReviewsQueryKey = [...getListReviewsQueryKey()];
export const performanceSkillsQueryKey = [...getListSkillsQueryKey()];

export function invalidateGoals(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: performanceGoalsQueryKey });
}

export function invalidateReviews(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: performanceReviewsQueryKey });
}

export function invalidateSkills(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: performanceSkillsQueryKey });
}

export function invalidateEmployeeSkills(
  queryClient: QueryClient,
  employeeId: string,
): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: [...getListEmployeeSkillsQueryKey(employeeId)],
  });
}

/** A catalog rename shows through every employee-skill row (`skill_name`). */
export function invalidateAllEmployeeSkills(queryClient: QueryClient): Promise<void> {
  // The generated employee-skill key is one joined URL string, so a segmented
  // array prefix cannot match it; predicate on the URL instead.
  return queryClient.invalidateQueries({
    predicate: (query) => {
      const key = query.queryKey[0];
      return (
        typeof key === "string" &&
        key.startsWith("/api/v1/performance/employees/") &&
        key.endsWith("/skills")
      );
    },
  });
}

export function invalidateReviewFeedback(
  queryClient: QueryClient,
  reviewId: string,
): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: [...getListReviewFeedbackQueryKey(reviewId)],
  });
}
