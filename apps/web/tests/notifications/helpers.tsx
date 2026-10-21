/**
 * Shared test plumbing for the notification-inbox suites — the generated
 * api-client is mocked wholesale per suite (inline `vi.mock` factories are
 * hoisted); hooks and components are the real thing (leave pattern).
 *
 * The generic render/wire helpers live in `tests/leave/helpers.tsx`; this file
 * re-exports them and adds the inbox's response shapes.
 */

import { vi } from "vitest";

import { requestPageState } from "../leave/helpers";

export { apiError, renderWithClient, wireMutation, wireQuery } from "../leave/helpers";
export type { MutateFn } from "../leave/helpers";

/** Paged `{ items, page }` response state for the inbox list. */
export function notificationPageState(items: unknown[], overrides: Record<string, unknown> = {}) {
  return requestPageState(items, overrides);
}

/** `{ unread_count }` response state for the header badge. */
export function unreadCountState(count: number, overrides: Record<string, unknown> = {}) {
  return {
    data: { data: { unread_count: count }, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}
