/**
 * Query-cache keys and invalidation helpers for the Reports feature.
 *
 * Every report query is keyed under `[reports, kind, params]` so switching a
 * tab, date, interval or grouping produces a distinct cache entry: changing a
 * filter refetches with the new key instead of mutating a stale render, and
 * the workspace can invalidate all four reports together if a future mutation
 * ever needs to. Reports are read-only today, so nothing calls the invalidator
 * yet — it exists so the surface never grows an ad-hoc fetch.
 */

import type { QueryClient } from "@tanstack/react-query";

import type { ReportKind, ReportParams } from "./types";

export const reportsQueryRoot = ["reports"] as const;

export function reportsQueryKey(kind: ReportKind, params: ReportParams) {
  return [...reportsQueryRoot, kind, params] as const;
}

export function invalidateReports(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: reportsQueryRoot });
}
