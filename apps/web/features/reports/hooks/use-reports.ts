"use client";

/**
 * Reports data hooks — thin wrappers over the generated client hooks.
 *
 * Every query is keyed `[reports, kind, params]`, so changing a tab or any
 * filter produces a new query instead of an ad-hoc fetch. `enabled` lets the
 * workspace mount all four hooks but only fetch the visible tab. The
 * organization hooks feed the dimension filter selects (archived rows stay
 * selectable: they can still own historical facts).
 */

import { useQueries } from "@tanstack/react-query";
import {
  getListDepartmentsQueryOptions,
  getListLocationsQueryOptions,
  getListPositionsQueryOptions,
  useGetAttendanceReport,
  useGetHeadcountReport,
  useGetLeaveReport,
  useGetOvertimeReport,
  useListDepartments,
  useListLocations,
  useListPositions,
} from "@workpulse/api-client";

import { reportsQueryKey } from "../api";
import type { ReportKind, ReportParams, ReportPayload } from "../types";
import {
  REPORT_FILTER_OPTION_MAX_PAGES,
  REPORT_FILTER_OPTION_PAGE_SIZE,
  filterOptionPageNumbers,
} from "../utils";

type ReportQueryOptions = {
  enabled?: boolean;
};

export function useOvertimeReport(
  params: ReportParams,
  { enabled = true }: ReportQueryOptions = {},
) {
  return useGetOvertimeReport(params, {
    query: { queryKey: reportsQueryKey("overtime", params), enabled },
  });
}

export function useAttendanceReport(
  params: ReportParams,
  { enabled = true }: ReportQueryOptions = {},
) {
  return useGetAttendanceReport(params, {
    query: { queryKey: reportsQueryKey("attendance", params), enabled },
  });
}

export function useLeaveReport(params: ReportParams, { enabled = true }: ReportQueryOptions = {}) {
  return useGetLeaveReport(params, {
    query: { queryKey: reportsQueryKey("leave", params), enabled },
  });
}

export function useHeadcountReport(
  params: ReportParams,
  { enabled = true }: ReportQueryOptions = {},
) {
  return useGetHeadcountReport(params, {
    query: { queryKey: reportsQueryKey("headcount", params), enabled },
  });
}

// --- Query state -----------------------------------------------------------

/** The one state shape the tab panel renders, whatever the report kind. */
export type ReportState = {
  kind: ReportKind;
  report: ReportPayload | null;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
};

type QueryLike = {
  data?: unknown;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
};

/**
 * Narrow one generated hook result to the shared state. Errors throw in the
 * transport (`ApiError`), so a settled response is either a 200 envelope or an
 * absent payload; the cast is local to this boundary.
 */
export function toReportState(kind: ReportKind, query: QueryLike): ReportState {
  const data = query.data as { status: number; data: ReportPayload } | undefined;
  return {
    kind,
    report: data?.status === 200 ? data.data : null,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    refetch: () => {
      void query.refetch();
    },
  };
}

// --- Filter options --------------------------------------------------------

export type ReportFilterOption = { id: string; name: string };

type NamedPage = {
  items: { id: string; name: string }[];
  page?: { total_pages?: number };
};

type NamedPageResponse = { status: number; data: NamedPage };

function namedPage(data: unknown): NamedPageResponse | undefined {
  if (typeof data !== "object" || data === null) return undefined;
  const response = data as { status?: unknown; data?: unknown };
  if (response.status !== 200) return undefined;
  if (typeof response.data !== "object" || response.data === null) return undefined;
  return response as NamedPageResponse;
}

function filterOptionPages(data: unknown): number[] {
  const total = namedPage(data)?.data.page?.total_pages ?? 1;
  return filterOptionPageNumbers(total);
}

function optionsFromPages(pages: readonly unknown[]): ReportFilterOption[] {
  return pages.flatMap((page) => {
    const response = namedPage(page);
    if (response === undefined) return [];
    return response.data.items.map((item) => ({ id: item.id, name: item.name }));
  });
}

function isTruncated(data: unknown): boolean {
  const total = namedPage(data)?.data.page?.total_pages ?? 1;
  return total > REPORT_FILTER_OPTION_MAX_PAGES;
}

/**
 * Departments, positions and locations for the three filter selects. The API
 * clamps each page to 100 rows, so this follows `total_pages` (up to a bounded
 * page count) instead of silently dropping dimensions past the first page.
 */
export function useReportFilterOptions(): {
  departments: ReportFilterOption[];
  positions: ReportFilterOption[];
  locations: ReportFilterOption[];
  truncated: boolean;
} {
  const departmentsFirst = useListDepartments({
    include_archived: true,
    page_size: REPORT_FILTER_OPTION_PAGE_SIZE,
    page: 1,
  });
  const positionsFirst = useListPositions({
    include_archived: true,
    page_size: REPORT_FILTER_OPTION_PAGE_SIZE,
    page: 1,
  });
  const locationsFirst = useListLocations({
    include_archived: true,
    page_size: REPORT_FILTER_OPTION_PAGE_SIZE,
    page: 1,
  });

  const departmentRest = useQueries({
    queries: filterOptionPages(departmentsFirst.data).map((page) =>
      getListDepartmentsQueryOptions({
        include_archived: true,
        page_size: REPORT_FILTER_OPTION_PAGE_SIZE,
        page,
      }),
    ),
  });
  const positionRest = useQueries({
    queries: filterOptionPages(positionsFirst.data).map((page) =>
      getListPositionsQueryOptions({
        include_archived: true,
        page_size: REPORT_FILTER_OPTION_PAGE_SIZE,
        page,
      }),
    ),
  });
  const locationRest = useQueries({
    queries: filterOptionPages(locationsFirst.data).map((page) =>
      getListLocationsQueryOptions({
        include_archived: true,
        page_size: REPORT_FILTER_OPTION_PAGE_SIZE,
        page,
      }),
    ),
  });

  return {
    departments: optionsFromPages([
      departmentsFirst.data,
      ...departmentRest.map((query) => query.data),
    ]),
    positions: optionsFromPages([positionsFirst.data, ...positionRest.map((query) => query.data)]),
    locations: optionsFromPages([locationsFirst.data, ...locationRest.map((query) => query.data)]),
    truncated:
      isTruncated(departmentsFirst.data) ||
      isTruncated(positionsFirst.data) ||
      isTruncated(locationsFirst.data),
  };
}
