/**
 * Query-cache keys and invalidation helpers for the recruitment feature.
 *
 * The generated client keys queries by URL + params, so invalidating with the
 * parameterless base key refreshes every variation — a job filter, the
 * candidate pipeline for another job and an interviewer's own list all settle
 * together after a transition.
 */

import type { QueryClient } from "@tanstack/react-query";
import {
  getListCandidatesQueryKey,
  getListInterviewsQueryKey,
  getListJobsQueryKey,
} from "@workpulse/api-client";

import { invalidateEmployees } from "@/features/employees/api";

export const recruitmentJobsQueryKey = [...getListJobsQueryKey()];
export const recruitmentCandidatesQueryKey = [...getListCandidatesQueryKey()];
export const recruitmentInterviewsQueryKey = [...getListInterviewsQueryKey()];

export function invalidateJobs(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: recruitmentJobsQueryKey });
}

export function invalidateCandidates(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: recruitmentCandidatesQueryKey });
}

export function invalidateInterviews(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: recruitmentInterviewsQueryKey });
}

/** Every recruitment list (a hire touches jobs, candidates and interviews). */
export async function invalidateRecruitment(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    invalidateJobs(queryClient),
    invalidateCandidates(queryClient),
    invalidateInterviews(queryClient),
  ]);
}

/** A hire also creates the Employee visible in the Employees workspace. */
export async function invalidateRecruitmentHire(queryClient: QueryClient): Promise<void> {
  await Promise.all([invalidateRecruitment(queryClient), invalidateEmployees(queryClient)]);
}
