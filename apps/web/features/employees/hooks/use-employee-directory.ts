"use client";

/**
 * One complete employee directory for name joins.
 *
 * The API caps a page at 100 rows, so a single 100-row request leaves tenants
 * above that with `Employee …id` fallbacks in dashboards and audit tables.
 * This shared query walks every page once, caches the result for a minute and
 * lets every surface (dashboard, attendance, employees) reuse the same cache
 * entry instead of fetching its own directory.
 *
 * The API remains the scope authority: a manager still receives only their
 * direct reports, and an unlinked/forbidden account receives an empty set.
 */

import { useQuery } from "@tanstack/react-query";
import { listEmployees } from "@workpulse/api-client";
import type { EmployeeSummary } from "@workpulse/api-client/index.schemas";

const PAGE_SIZE = 100;

async function fetchAllEmployees(): Promise<EmployeeSummary[]> {
  const first = await listEmployees({ page: 1, page_size: PAGE_SIZE });
  if (first.status !== 200) return [];
  const totalPages = first.data.page.total_pages;
  if (totalPages <= 1) return first.data.items;

  const rest = await Promise.all(
    Array.from({ length: totalPages - 1 }, (_, index) =>
      listEmployees({ page: index + 2, page_size: PAGE_SIZE }),
    ),
  );
  return [first, ...rest].flatMap((response) =>
    response.status === 200 ? response.data.items : [],
  );
}

export function useEmployeeDirectory(enabled = true) {
  return useQuery({
    queryKey: ["employees", "directory", "all"],
    queryFn: fetchAllEmployees,
    enabled,
    staleTime: 60_000,
  });
}
