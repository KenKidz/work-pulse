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
  getListDepartmentsQueryOptions,
  getListLocationsQueryOptions,
  getListPositionsQueryOptions,
  useCloseJob,
  useCreateJob,
  useListDepartments,
  useListJobs,
  useListLocations,
  useListPositions,
  useOpenJob,
  useUpdateJob,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { useAuth } from "@/features/auth/components/AuthGate";

import { invalidateJobs } from "../api";
import { recruitmentErrorMessage } from "../labels";
import { RECRUITMENT_MANAGER_ROLES, RECRUITMENT_NAV_ROLES } from "../roles";
import type { Department, ListJobsParams, Location, Position } from "../types";

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

export function useJobs(params?: ListJobsParams) {
  return useListJobs(params);
}

export type JobFieldName =
  "title" | "description" | "department_id" | "position_id" | "location_id" | "openings" | "root";

export type JobFieldErrors = Partial<Record<JobFieldName, string>>;

/** Map a pinned job error code to the field/dialog that renders it. */
export function jobFieldErrors(error: unknown): JobFieldErrors {
  const message = recruitmentErrorMessage(error);
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
  const queryClient = useQueryClient();
  return useCreateJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success("Job created");
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error)),
    },
  });
}

export function useJobUpdate() {
  const queryClient = useQueryClient();
  return useUpdateJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success("Job updated");
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error)),
    },
  });
}

export function useJobOpen() {
  const queryClient = useQueryClient();
  return useOpenJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success("Job opened");
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error)),
    },
  });
}

export function useJobClose() {
  const queryClient = useQueryClient();
  return useCloseJob({
    mutation: {
      onSuccess: async () => {
        await invalidateJobs(queryClient);
        toast.success("Job closed");
      },
      onError: (error) => toast.error(recruitmentErrorMessage(error)),
    },
  });
}
