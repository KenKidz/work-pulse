/**
 * "My interviews" — the assigned interviewer's scoped list, the empty state
 * and feedback completion (which refreshes the query key so the row becomes
 * `completed` without a reload). The generated api-client is mocked wholesale.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useCompleteInterview,
  useListCandidates,
  useListInterviews,
  useListJobs,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { MyInterviewsPanel } from "@/features/recruitment/components/MyInterviewsPanel";
import type { Candidate, Interview, Job } from "@/features/recruitment/types";

import {
  apiError,
  renderWithClient,
  requestPageState,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

const MANAGER_EMPLOYEE_ID = "01900000-0000-7000-8000-0000000000a1";

const mocks = vi.hoisted(() => ({
  user: {
    role: "manager",
    employee_id: "01900000-0000-7000-8000-0000000000a1" as string | null,
  },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => ({ user: mocks.user }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  // Query keys used by the feature invalidation helpers.
  getListJobsQueryKey: () => ["/api/v1/recruitment/jobs"],
  getListCandidatesQueryKey: () => ["/api/v1/recruitment/candidates"],
  getListInterviewsQueryKey: () => ["/api/v1/recruitment/interviews"],
  getListEmployeesQueryKey: () => ["/api/v1/employees"],
  getGetEmployeeQueryKey: (id: string) => ["/api/v1/employees", id],
  getListEmploymentsQueryKey: (id: string) => ["/api/v1/employees", id, "employments"],
  // Directory paging (only invoked when a first page has more pages).
  getListJobsQueryOptions: vi.fn(),
  getListCandidatesQueryOptions: vi.fn(),
  getListDepartmentsQueryOptions: vi.fn(),
  getListPositionsQueryOptions: vi.fn(),
  getListLocationsQueryOptions: vi.fn(),
  // Hooks used by this surface and the shared recruitment module.
  useListInterviews: vi.fn(),
  useCompleteInterview: vi.fn(),
  useListCandidates: vi.fn(),
  useListJobs: vi.fn(),
  useListEmployees: vi.fn(),
  useListDepartments: vi.fn(),
  useListPositions: vi.fn(),
  useListLocations: vi.fn(),
  useCreateJob: vi.fn(),
  useUpdateJob: vi.fn(),
  useOpenJob: vi.fn(),
  useCloseJob: vi.fn(),
  useCreateCandidate: vi.fn(),
  useUpdateCandidate: vi.fn(),
  useAdvanceCandidate: vi.fn(),
  useRejectCandidate: vi.fn(),
  useWithdrawCandidate: vi.fn(),
  useHireCandidate: vi.fn(),
  useScheduleInterview: vi.fn(),
  useUpdateInterview: vi.fn(),
  useCancelInterview: vi.fn(),
}));

const TENANT_ID = "01900000-0000-7000-8000-000000000001";

const JOB: Job = {
  id: "01900000-0000-7000-8000-0000000000b1",
  tenant_id: TENANT_ID,
  title: "Backend Engineer",
  description: null,
  department_id: "01900000-0000-7000-8000-0000000000d1",
  position_id: "01900000-0000-7000-8000-0000000000e1",
  location_id: "01900000-0000-7000-8000-0000000000f1",
  openings: 1,
  status: "open",
  created_by: "01900000-0000-7000-8000-000000000014",
  opened_at: "2026-10-20T09:00:00Z",
  closed_at: null,
  created_at: "2026-10-19T09:00:00Z",
  updated_at: "2026-10-20T09:00:00Z",
};

const CANDIDATE: Candidate = {
  id: "01900000-0000-7000-8000-0000000000c1",
  tenant_id: TENANT_ID,
  job_id: JOB.id,
  first_name: "Bao",
  last_name: "Nguyen",
  email: "bao.nguyen@example.test",
  phone: null,
  source: "job_board",
  notes: null,
  status: "interviewing",
  stage_note: null,
  rejected_from: null,
  hired_at: null,
  employee_id: null,
  created_at: "2026-10-22T09:00:00Z",
  updated_at: "2026-10-22T09:00:00Z",
};

function interview(overrides: Partial<Interview>): Interview {
  return {
    id: "",
    tenant_id: TENANT_ID,
    candidate_id: CANDIDATE.id,
    job_id: JOB.id,
    interviewer_id: MANAGER_EMPLOYEE_ID,
    scheduled_at: "2026-11-02T14:30:00Z",
    notes: null,
    status: "scheduled",
    rating: null,
    feedback_notes: null,
    completed_at: null,
    cancelled_at: null,
    created_at: "2026-10-25T09:00:00Z",
    updated_at: "2026-10-25T09:00:00Z",
    ...overrides,
  };
}

const SCHEDULED = interview({ id: "01900000-0000-7000-8000-0000000000f1" });

const COMPLETED = interview({
  id: "01900000-0000-7000-8000-0000000000f2",
  status: "completed",
  rating: 5,
  feedback_notes: "Excellent depth.",
  completed_at: "2026-11-02T15:30:00Z",
});

const completeInterview = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  mocks.user = { role: "manager", employee_id: MANAGER_EMPLOYEE_ID };
  wireQuery(useListInterviews, requestPageState([SCHEDULED, COMPLETED]));
  wireQuery(useListCandidates, requestPageState([CANDIDATE]));
  wireQuery(useListJobs, requestPageState([JOB]));
  wireMutation(useCompleteInterview as unknown as MutateFn, completeInterview);
});

describe("MyInterviewsPanel", () => {
  it("lists only the caller's scoped interviews with candidate and job labels", async () => {
    renderWithClient(<MyInterviewsPanel />);

    expect(await screen.findAllByText("Bao Nguyen")).toHaveLength(2);
    expect(screen.getAllByText("Backend Engineer")).toHaveLength(2);
    expect(useListInterviews).toHaveBeenCalledWith({ page: 1, page_size: 20, mine: true });
    expect(
      screen.getByText("Scheduled", { selector: "[data-status='scheduled']" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Completed", { selector: "[data-status='completed']" }),
    ).toBeInTheDocument();
    expect(screen.getByText("5 / 5")).toBeInTheDocument();
    expect(screen.getByText("Excellent depth.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reschedule|Cancel interview/ })).toBeNull();
  });

  it("submits feedback and invalidates the interviews key so the row settles", async () => {
    const user = userEvent.setup();
    completeInterview.mockResolvedValue({ data: { status: 200 } });
    const { queryClient } = renderWithClient(<MyInterviewsPanel />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await user.click(
      await screen.findByRole("button", { name: "Submit feedback for Bao Nguyen" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Submit feedback" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Rating" }));
    await user.click(await screen.findByRole("option", { name: "4 — Strong" }));
    fireEvent.change(within(dialog).getByLabelText("Feedback notes (optional)"), {
      target: { value: "Solid trade-off reasoning." },
    });
    await user.click(within(dialog).getByRole("button", { name: "Submit feedback" }));

    await waitFor(() =>
      expect(completeInterview).toHaveBeenCalledWith({
        interviewId: SCHEDULED.id,
        data: { rating: 4, notes: "Solid trade-off reasoning." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Feedback submitted");
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["/api/v1/recruitment/interviews"],
      }),
    );
  });

  it("keeps a wrong-state refusal visible in the dialog", async () => {
    const user = userEvent.setup();
    completeInterview.mockRejectedValue(
      apiError(409, "interview_not_scheduled", "Already completed"),
    );
    renderWithClient(<MyInterviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Submit feedback for Bao Nguyen" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Rating" }));
    await user.click(await screen.findByRole("option", { name: "4 — Strong" }));
    await user.click(within(dialog).getByRole("button", { name: "Submit feedback" }));

    expect(
      await within(dialog).findByText(
        "This interview is no longer scheduled. Refresh and try again.",
      ),
    ).toBeInTheDocument();
    expect(dialog).toBeVisible();
  });

  it("renders the empty state when no interview is assigned", async () => {
    wireQuery(useListInterviews, requestPageState([]));
    renderWithClient(<MyInterviewsPanel />);

    expect(
      await screen.findByText(
        "No interviews are assigned to you yet. When HR schedules one, it appears here.",
      ),
    ).toBeInTheDocument();
  });
});
