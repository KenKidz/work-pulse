/**
 * The generated `@workpulse/api-client` surface used by the performance
 * feature, mocked wholesale for the web suites. Every hook is a `vi.fn`; each
 * test wires only the hooks its surface calls, and the real feature hooks,
 * error mapping and components stay in play (leave/recruitment pattern).
 */

import { vi } from "vitest";

export function createPerformanceApiClientMock() {
  return {
    // Query keys used by the feature invalidation helpers.
    getListGoalsQueryKey: () => ["/api/v1/performance/goals"],
    getListReviewsQueryKey: () => ["/api/v1/performance/reviews"],
    getListSkillsQueryKey: () => ["/api/v1/performance/skills"],
    getListEmployeeSkillsQueryKey: (employeeId: string) => [
      `/api/v1/performance/employees/${employeeId}/skills`,
    ],
    getListReviewFeedbackQueryKey: (reviewId: string) => [
      "/api/v1/performance/reviews",
      reviewId,
      "feedback",
    ],
    // Directory and list queries.
    useListEmployees: vi.fn(),
    useGetCurrentEmployee: vi.fn(),
    useListGoals: vi.fn(),
    useListReviews: vi.fn(),
    useListSkills: vi.fn(),
    useListEmployeeSkills: vi.fn(),
    useListReviewFeedback: vi.fn(),
    // Goal mutations.
    useCreateGoal: vi.fn(),
    useUpdateGoal: vi.fn(),
    useActivateGoal: vi.fn(),
    useCompleteGoal: vi.fn(),
    useCancelGoal: vi.fn(),
    // Review and feedback mutations.
    useCreateReview: vi.fn(),
    useUpdateReview: vi.fn(),
    useCompleteReview: vi.fn(),
    useCancelReview: vi.fn(),
    useSubmitReviewFeedback: vi.fn(),
    // Catalog and employee-skill mutations.
    useCreateSkill: vi.fn(),
    useUpdateSkill: vi.fn(),
    useArchiveSkill: vi.fn(),
    useRestoreSkill: vi.fn(),
    useRecordEmployeeSkill: vi.fn(),
    useUpdateEmployeeSkill: vi.fn(),
    useRemoveEmployeeSkill: vi.fn(),
  };
}
