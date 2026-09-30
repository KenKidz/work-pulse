/**
 * Shared test plumbing for the auth feature suites — the generated
 * api-client is mocked wholesale (inline per suite, vi.mock factories are
 * hoisted); hooks, error mapping and components are the real thing
 * (pattern from the employees/organizations suites).
 */

import { vi, type Mock } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import type { User } from "@workpulse/api-client/index.schemas";

/** Wire a mocked generated query hook to a state object. */
export function wireQuery(hook: unknown, state: unknown): void {
  (hook as ReturnType<typeof vi.fn>).mockReturnValue(state);
}

/** Build the query-state shape the generated `useGetCurrentUser` returns. */
export function authState(user: User | null, overrides: Record<string, unknown> = {}) {
  if (user === null) {
    return {
      data: undefined,
      isPending: false,
      isError: true,
      error: new ApiError(401, "unauthenticated", "Not authenticated"),
      refetch: vi.fn(),
      ...overrides,
    };
  }
  return {
    data: { data: user, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
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

/** ApiError with the pinned failure code (visible-error fixtures). */
export function apiError(status: number, code: string, message: string): ApiError {
  return new ApiError(status, code, message);
}
