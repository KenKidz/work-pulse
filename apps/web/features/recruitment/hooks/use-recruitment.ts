"use client";

/**
 * Recruitment data hooks — thin wrappers over the generated client hooks.
 *
 * The workspace guard mirrors the domain policies (the API stays the
 * authority): Employees and Finance are redirected, a Manager reaches only
 * the assigned-interview view, and HR/Admin/Super Admin run every tab.
 * Mutations invalidate the URL-keyed list caches and toast the outcome;
 * `ApiError.code` maps back to the job form so the server stays the authority.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import {
  getListCandidatesQueryOptions,
  getListDepartmentsQueryOptions,
  getListJobsQueryOptions,
  getListLocationsQueryOptions,
  getListPositionsQueryOptions,
  useAdvanceCandidate,
  useCancelInterview,
  useCloseJob,
  useCompleteInterview,
  useCreateCandidate,
  useCreateJob,
  useHireCandidate,
  useListCandidates,
  useListDepartments,
  useListEmployees,
  useListInterviews,
  useListJobs,
  useListLocations,
  useListPositions,
  useOpenJob,
  useRejectCandidate,
  useScheduleInterview,
  useUpdateCandidate,
  useUpdateInterview,
  useUpdateJob,
  useWithdrawCandidate,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useAuth } from "@/features/auth/components/AuthGate";

import { useApiErrorTranslator } from "@/lib/api-errors";

import {
  invalidateInterviews,
  invalidateJobs,
  invalidateRecruitment,
  invalidateRecruitmentHire,
} from "../api";
import { recruitmentErrorMessage } from "../labels";
import { RECRUITMENT_MANAGER_ROLES, RECRUITMENT_NAV_ROLES } from "../roles";
import type {
  Candidate,
  Department,
  EmployeeSummary,
  Job,
  ListCandidatesParams,
  ListInterviewsParams,
  ListJobsParams,
  Location,
  Position,
} from "../types";

/**
 * The web-side recruitment gate. Employees and Finance are redirected to the
 * dashboard (they have no standing on this surface); everyone else gets a
 * view of their permitted tabs.
 */
export function useRecruitmentAccess() {
  const { user } = useAuth();
  const router = useRouter();
  const canView = RECRUITMENT_NAV_ROLES.includes(user.role);
  const canManage = RECRUITMENT_MANAGER_ROLES.includes(user.role);

  useEffect(() => {
    if (!canView) {
      router.replace("/dashboard");
    }
  }, [canView, router]);

  return { user, canView, canManage };
}

/**
 * Organization references for job filters, pickers and row labels. Archived
 * rows stay in the maps so a job that outlived a department/position/location
 * still renders its real name; pickers disable them unless already selected.
 *
 * The API clamps each page to 100 rows, so the directory follows `total_pages`
 * (bounded, like the reports filter options) instead of silently dropping
 * references past the first page.
 */
const DIRECTORY_PAGE_SIZE = 100;
const DIRECTORY_MAX_PAGES = 20;

type DirectoryPage = {
  items: unknown[];
  totalPages: number;
};

function readDirectoryPage(data: unknown): DirectoryPage | null {
  if (typeof data !== "object" || data === null) return null;
  const response = data as {
    status?: unknown;
    data?: { items?: unknown; page?: { total_pages?: unknown } };
  };
  if (response.status !== 200 || !Array.isArray(response.data?.items)) return null;
  const totalPages = response.data?.page?.total_pages;
  return {
    items: response.data.items,
    totalPages: typeof totalPages === "number" ? totalPages : 1,
  };
}

/** Page numbers after the first, bounded so a pathological list cannot fan out. */
function directoryPageNumbers(data: unknown): number[] {
  const totalPages = readDirectoryPage(data)?.totalPages ?? 1;
  const capped = Math.min(Math.max(totalPages, 1), DIRECTORY_MAX_PAGES);
  return Array.from({ length: capped - 1 }, (_, index) => index + 2);
}

function directoryItems<T>(pages: readonly unknown[]): T[] {
  return pages.flatMap((page) => {
    const parsed = readDirectoryPage(page);
    return parsed === null ? [] : (parsed.items as T[]);
  });
}

export function useRecruitmentDirectory() {
  const departmentsFirst = useListDepartments({
    include_archived: true,
    page_size: DIRECTORY_PAGE_SIZE,
    page: 1,
  });
  const positionsFirst = useListPositions({
    include_archived: true,
    page_size: DIRECTORY_PAGE_SIZE,
    page: 1,
  });
  const locationsFirst = useListLocations({
    include_archived: true,
    page_size: DIRECTORY_PAGE_SIZE,
    page: 1,
  });

  const departmentsRest = useQueries({
    queries: directoryPageNumbers(departmentsFirst.data).map((page) =>
      getListDepartmentsQueryOptions({
        include_archived: true,
        page_size: DIRECTORY_PAGE_SIZE,
        page,
      }),
    ),
  });
  const positionsRest = useQueries({
    queries: directoryPageNumbers(positionsFirst.data).map((page) =>
      getListPositionsQueryOptions({
        include_archived: true,
        page_size: DIRECTORY_PAGE_SIZE,
        page,
      }),
    ),
  });
  const locationsRest = useQueries({
    queries: directoryPageNumbers(locationsFirst.data).map((page) =>
      getListLocationsQueryOptions({
        include_archived: true,
        page_size: DIRECTORY_PAGE_SIZE,
        page,
      }),
    ),
  });

  const departments = directoryItems<Department>([
    departmentsFirst.data,
    ...departmentsRest.map((query) => query.data),
  ]);
  const positions = directoryItems<Position>([
    positionsFirst.data,
    ...positionsRest.map((query) => query.data),
  ]);
  const locations = directoryItems<Location>([
    locationsFirst.data,
    ...locationsRest.map((query) => query.data),
  ]);

  return {
    departments,
    positions,
    locations,
    departmentNames: new Map(departments.map((row) => [row.id, row.name])),
    positionNames: new Map(positions.map((row) => [row.id, row.title])),
    locationNames: new Map(locations.map((row) => [row.id, row.name])),
  };
}

/**
 * Job titles for the candidate pipeline — the filter picker, the `open` intake
 * choices and the row labels. Every status is included so a candidate on a
 * closed job still resolves its title; the directory follows `total_pages`
 * with the same bounded fan-out as the organization directory.
 */
export function useRecruitmentJobDirectory() {
  const first = useListJobs({ page: 1, page_size: DIRECTORY_PAGE_SIZE });
  const rest = useQueries({
    queries: directoryPageNumbers(first.data).map((page) =>
      getListJobsQueryOptions({ page, page_size: DIRECTORY_PAGE_SIZE }),
    ),
  });

  const jobs = directoryItems<Job>([first.data, ...rest.map((query) => query.data)]);

  return {
    jobs,
    openJobs: jobs.filter((job) => job.status === "open"),
    jobNames: new Map(jobs.map((job) => [job.id, job.title])),
    isPending: first.isPending,
  };
}

export function useJobs(params?: ListJobsParams) {
  return useListJobs(params);
}

export function useCandidates(params?: ListCandidatesParams) {
  return useListCandidates(params);
}

export function useInterviews(params?: ListInterviewsParams) {
  return useListInterviews(params);
}

/**
 * Candidate labels and the scheduling choices. The API scopes the list for a
 * non-management caller to the candidates they interview, so the same hook
 * serves HR's pipeline and an interviewer's "My interviews" without exposing
 * anyone else's rows. Follows `total_pages` with the bounded directory fan-out.
 */
export function useRecruitmentCandidateDirectory() {
  const first = useListCandidates({ page: 1, page_size: DIRECTORY_PAGE_SIZE });
  const rest = useQueries({
    queries: directoryPageNumbers(first.data).map((page) =>
      getListCandidatesQueryOptions({ page, page_size: DIRECTORY_PAGE_SIZE }),
    ),
  });

  const candidates = directoryItems<Candidate>([first.data, ...rest.map((query) => query.data)]);

  return {
    interviewingCandidates: candidates.filter((row) => row.status === "interviewing"),
    candidateNames: new Map(
      candidates.map((row) => [row.id, `${row.first_name} ${row.last_name}`]),
    ),
    isPending: first.isPending,
  };
}

/**
 * The active employee directory behind the hire dialog's manager picker. The
 * server filters to active employees; the list is bounded to one page like the
 * employees form's picker.
 */
export function useActiveEmployees(): EmployeeSummary[] {
  const query = useListEmployees({ status: "active", page_size: 100 });
  return query.data?.status === 200 ? query.data.data.items : [];
}

export type JobFieldName =
  "title" | "description" | "department_id" | "position_id" | "location_id" | "openings" | "root";

export type JobFieldErrors = Partial<Record<JobFieldName, string>>;

/** Map a pinned job error code to the field/dialog that renders it. */
export function jobFieldErrors(
  error: unknown,
  t: (
    key: Parameters<typeof recruitmentErrorMessage>[1] extends (key: infer K) => string ? K : never,
  ) => string,
): JobFieldErrors {
  const message = recruitmentErrorMessage(error, t);
  if (error instanceof ApiError) {
    switch (error.code) {
      case "department_not_found":
        return { department_id: message };
      case "position_not_found":
        return { position_id: message };
      case "location_not_found":
        return { location_id: message };
      default:
        return { root: message };
    }
  }
  return { root: message };
}

export function useJobCreate() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useCreateJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success(t("jobCreated"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useJobUpdate() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useUpdateJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success(t("jobUpdated"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useJobOpen() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useOpenJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success(t("jobOpened"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useJobClose() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useCloseJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success(t("jobClosed"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export type CandidateFieldName =
  "job_id" | "first_name" | "last_name" | "email" | "phone" | "source" | "notes" | "root";

export type CandidateFieldErrors = Partial<Record<CandidateFieldName, string>>;

/** Map a pinned intake/edit error code to the field that renders it. */
export function candidateFieldErrors(
  error: unknown,
  t: Parameters<typeof recruitmentErrorMessage>[1],
): CandidateFieldErrors {
  const message = recruitmentErrorMessage(error, t);
  if (error instanceof ApiError) {
    switch (error.code) {
      case "candidate_already_applied":
        return { email: message };
      case "job_not_open":
      case "job_not_found":
        return { job_id: message };
      default:
        return { root: message };
    }
  }
  return { root: message };
}

export type HireFieldName =
  | "employment_type"
  | "hired_at"
  | "manager_id"
  | "employee_code"
  | "first_name"
  | "last_name"
  | "email"
  | "phone"
  | "root";

export type HireFieldErrors = Partial<Record<HireFieldName, string>>;

/**
 * Map the hire seam's propagated codes back to the field that caused them.
 * The job's organization references are not editable here, so those failures
 * stay on the dialog root with the seam's sentence.
 */
export function hireFieldErrors(
  error: unknown,
  t: Parameters<typeof recruitmentErrorMessage>[1],
): HireFieldErrors {
  const message = recruitmentErrorMessage(error, t);
  if (error instanceof ApiError) {
    switch (error.code) {
      case "employee_email_taken":
        return { email: message };
      case "employee_code_taken":
        return { employee_code: message };
      case "employee_manager_invalid":
        return { manager_id: message };
      case "validation_error":
        return { hired_at: message };
      default:
        return { root: message };
    }
  }
  return { root: message };
}

export function useCandidateCreate() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useCreateCandidate({
    mutation: {
      onSuccess: async () => {
        await invalidateRecruitment(queryClient);
        toast.success(t("candidateAdded"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useCandidateUpdate() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useUpdateCandidate({
    mutation: {
      onSuccess: async () => {
        await invalidateRecruitment(queryClient);
        toast.success(t("candidateUpdated"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useCandidateAdvance() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useAdvanceCandidate({
    mutation: {
      onSuccess: async () => {
        await invalidateRecruitment(queryClient);
        toast.success(t("candidateAdvanced"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useCandidateReject() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useRejectCandidate({
    mutation: {
      onSuccess: async () => {
        await invalidateRecruitment(queryClient);
        toast.success(t("candidateRejected"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useCandidateWithdraw() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useWithdrawCandidate({
    mutation: {
      onSuccess: async () => {
        await invalidateRecruitment(queryClient);
        toast.success(t("candidateWithdrawn"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useCandidateHire() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useHireCandidate({
    mutation: {
      onSuccess: async () => {
        // A hire also creates the Employee through the seam.
        await invalidateRecruitmentHire(queryClient);
        toast.success(t("candidateHired"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export type InterviewFieldName =
  "candidate_id" | "interviewer_id" | "scheduled_at" | "notes" | "rating" | "root";

export type InterviewFieldErrors = Partial<Record<InterviewFieldName, string>>;

/** Map the pinned interview codes back to the field that caused them. */
export function interviewFieldErrors(
  error: unknown,
  t: Parameters<typeof recruitmentErrorMessage>[1],
): InterviewFieldErrors {
  const message = recruitmentErrorMessage(error, t);
  if (error instanceof ApiError) {
    switch (error.code) {
      case "interviewer_not_found":
      case "interviewer_not_active":
        return { interviewer_id: message };
      case "candidate_stage_conflict":
      case "candidate_not_found":
        return { candidate_id: message };
      default:
        return { root: message };
    }
  }
  return { root: message };
}

export function useInterviewSchedule() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useScheduleInterview({
    mutation: {
      onSuccess: async () => {
        await invalidateInterviews(queryClient);
        toast.success(t("interviewScheduled"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useInterviewUpdate() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useUpdateInterview({
    mutation: {
      onSuccess: async () => {
        await invalidateInterviews(queryClient);
        toast.success(t("interviewUpdated"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useInterviewComplete() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useCompleteInterview({
    mutation: {
      onSuccess: async () => {
        // Invalidation refetches the list, so the row flips to `completed`
        // without a reload (the interviewer feedback AC).
        await invalidateInterviews(queryClient);
        toast.success(t("feedbackSubmitted"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}

export function useInterviewCancel() {
  const t = useTranslations("recruitment.toasts");
  const errors = useApiErrorTranslator("recruitment.errors");
  const queryClient = useQueryClient();
  return useCancelInterview({
    mutation: {
      onSuccess: async () => {
        await invalidateInterviews(queryClient);
        toast.success(t("interviewCancelled"));
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error, errors)),
    },
  });
}
