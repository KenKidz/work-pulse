/**
 * Shared test plumbing for the leave feature suites — the generated
 * api-client is mocked wholesale (inline per suite, vi.mock factories are
 * hoisted); hooks, error mapping and components are the real thing
 * (attendance/employees pattern).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi, type Mock } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

/** Wire a mocked generated query hook to a state object. */
export function wireQuery(hook: unknown, state: unknown): void {
  (hook as ReturnType<typeof vi.fn>).mockReturnValue(state);
}

export type MutateFn = Mock<(variables: unknown) => Promise<unknown>>;

export type MutationOptionsLike = {
  mutation?: {
    onSuccess?: (data: unknown, variables: unknown, context: unknown) => unknown;
    onError?: (error: unknown, variables: unknown, context: unknown) => unknown;
  };
};

/** Wire a mocked generated mutation hook to a controllable mutateAsync fn. */
export function wireMutation(hook: unknown, mutateAsync: MutateFn): void {
  const impl = (options?: MutationOptionsLike) => {
    async function run(variables: unknown, rethrow: boolean) {
      try {
        const result = await mutateAsync(variables);
        await options?.mutation?.onSuccess?.(result, variables, undefined);
        return result;
      } catch (error) {
        await options?.mutation?.onError?.(error, variables, undefined);
        if (rethrow) throw error;
        return undefined;
      }
    }
    return {
      mutate: (variables: unknown) => {
        void run(variables, false);
      },
      mutateAsync: (variables: unknown) => run(variables, true),
      isPending: false,
    };
  };
  (hook as ReturnType<typeof vi.fn>).mockImplementation(impl as never);
}

/** Paged `{ items, page }` response state (leave requests). */
export function requestPageState(items: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    data: {
      data: {
        items,
        page: { page: 1, page_size: 20, total_items: items.length, total_pages: 1 },
      },
      status: 200,
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

/** Bare array response state (`LeaveType[]`, `LeaveBalance[]`). */
export function listState(items: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    data: { data: items, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

/** Single-resource response state (`/employees/me`). */
export function itemState(item: unknown, overrides: Record<string, unknown> = {}) {
  return {
    data: { data: item, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

/** ApiError with a pinned failure code (visible-error fixtures). */
export function apiError(status: number, code: string, message: string): ApiError {
  return new ApiError(status, code, message);
}

/** Render inside a QueryClientProvider (retry off — asserts stay deterministic). */
export function renderWithClient(ui: ReactElement): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}
