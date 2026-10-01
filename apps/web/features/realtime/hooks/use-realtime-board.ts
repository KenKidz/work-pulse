"use client";

/**
 * REST snapshot hook — initial render and degraded mode (decision 7).
 *
 * Refetches about every 60s so clock-driven changes (absent onset) land even
 * while the socket is down; the socket resyncs this same cache entry on
 * reconnect. Disabled for roles the page is about to redirect away.
 */

import { useGetRealtimeBoard } from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { REALTIME_BOARD_REFRESH_MS } from "../api";

export function useRealtimeBoard(enabled: boolean) {
  return useGetRealtimeBoard({
    query: { enabled, refetchInterval: REALTIME_BOARD_REFRESH_MS },
  });
}

/** Shared role-denied classification (mirrors the attendance board hook). */
export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.code === "forbidden");
}
