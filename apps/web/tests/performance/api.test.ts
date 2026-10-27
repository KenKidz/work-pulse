/**
 * The employee-skill query key is one joined URL string, so catalog renames
 * invalidate it through a predicate rather than a segmented array prefix. This
 * guards the regression that the generated key shape can hide.
 */

import { QueryClient } from "@tanstack/react-query";
import { getListEmployeeSkillsQueryKey } from "@workpulse/api-client";
import { describe, expect, it, vi } from "vitest";

import { invalidateAllEmployeeSkills } from "@/features/performance/api";

describe("performance query invalidation", () => {
  it("matches the generated employee-skill URL key", async () => {
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await invalidateAllEmployeeSkills(queryClient);

    const filters = invalidate.mock.calls[0]?.[0] as
      { predicate?: (query: { queryKey: readonly unknown[] }) => boolean } | undefined;
    expect(filters?.predicate).toBeTypeOf("function");

    expect(
      filters?.predicate?.({
        queryKey: getListEmployeeSkillsQueryKey("01900000-0000-7000-8000-0000000000a1"),
      }),
    ).toBe(true);
    expect(
      filters?.predicate?.({
        queryKey: ["/api/v1/performance/reviews"],
      }),
    ).toBe(false);
    expect(
      filters?.predicate?.({
        queryKey: ["/api/v1/performance/employees/01900000-0000-7000-8000-0000000000a1/goals"],
      }),
    ).toBe(false);
  });
});
