/**
 * Query-cache key and refresh cadence for the command-center board.
 *
 * The REST snapshot and the socket write the same generated URL key, so
 * server data stays in the Query cache — no shadow copies in state
 * (`.ai/conventions.md`).
 */

import type { QueryClient } from "@tanstack/react-query";
import { getGetRealtimeBoardQueryKey } from "@workpulse/api-client";

export const realtimeBoardQueryKey = [...getGetRealtimeBoardQueryKey()];

/**
 * Slow snapshot refresh — clock-driven transitions (absent onset at
 * start + grace) push nothing, so they land within a minute (decision 6).
 */
export const REALTIME_BOARD_REFRESH_MS = 60_000;

export function invalidateRealtimeBoard(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: realtimeBoardQueryKey });
}
