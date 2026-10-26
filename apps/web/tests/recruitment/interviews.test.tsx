/**
 * Interviews tab — schedule/reschedule/cancel, the assigned interviewer's
 * feedback completion, the pinned error sentences and the loading/empty/error
 * states. The generated api-client is mocked wholesale.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useCancelInterview,
  useCompleteInterview,
  useListCandidates,
  useListEmployees,
  useListInterviews,
  useListJobs,
  useScheduleInterview,
  useUpdateInterview,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { InterviewsPanel } from "@/features/recruitment/components/InterviewsPanel";
import { localDateTimeToUtc } from "@/features/recruitment/dates";
import type {
  Candidate,
  EmployeeSummary,
  Interview,
  Job,
} from "@/features/recruitment/types";

import {
  apiError,
  renderWithClient,
  requestPageState,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

const mocks = vi.hoisted(() => ({
  user: { role: "hr", employee_id: "01900000-0000-7000-8000-0000000000a1" as string | null },
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
  useScheduleInterview: vi.fn(),
  useUpdateInterview: vi.fn(),
  useCompleteInterview: vi.fn(),
  useCancelInterview: vi.fn(),
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
}));

const TENANT_ID = "01900000-0000-7000-8000-000000000001";

const JOB: Job = {
  id: "01900000-0000-7000-8000-0000000000b1",
  tenant_id: TENANT_ID,
  title: "Backend Engineer",
  description: "Own the workforce API.",
  department_id: "01900000-0000-7000-8000-0000000000d1",
  position_id: "01900000-0000-7000-8000-0000000000e1",
  location_id: "01900000-0000-7000-8000-0000000000f1",
  openings: 2,
  status: "open",
  created_by: "01900000-0000-7000-8000-000000000014",
  opened_at: "2026-10-20T09:00:00Z",
  closed_at: null,
  created_at: "2026-10-19T09:00:00Z",
  updated_at: "2026-10-20T09:00:00Z",
};

const MANAGER: EmployeeSummary = {
  id: "01900000-0000-7000-8000-0000000000a1",
  employee_code: "EMP-00009",
  first_name: "Linh",
  last_name: "Tran",
  email: "linh.tran@example.test",
  department_id: JOB.department_id,
  position_id: JOB.position_id,
  location_id: JOB.location_id,
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2024-01-15",
  terminated_at: null,
};

const OTHER_EMPLOYEE: EmployeeSummary = {
  ...MANAGER,
  id: "01900000-0000-7000-8000-0000000000a2",
  employee_code: "EMP-00010",
  first_name: "Omar",
  last_name: "Rahman",
  email: "omar.rahman@example.test",
};

function candidate(overrides: Partial<Candidate>): Candidate {
  return {
    id: "",
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
    ...overrides,
  };
}

const INTERVIEWING = candidate({ id: "01900000-0000-7000-8000-0000000000c1" });

const APPLIED = candidate({
  id: "01900000-0000-7000-8000-0000000000c2",
  first_name: "Amara",
  last_name: "Okafor",
  email: "amara.okafor@example.test",
  status: "applied",
});

function interview(overrides: Partial<Interview>): Interview {
  return {
    id: "",
    tenant_id: TENANT_ID,
    candidate_id: INTERVIEWING.id,
    job_id: JOB.id,
    interviewer_id: MANAGER.id,
    scheduled_at: "2026-11-02T14:30:00Z",
    notes: "System design conversation.",
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
  rating: 4,
  feedback_notes: "Strong systems thinking; follow up on scale.",
  completed_at: "2026-11-02T15:30:00Z",
});

const scheduleInterview = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateInterview = vi.fn<(variables: unknown) => Promise<unknown>>();
const completeInterview = vi.fn<(variables: unknown) => Promise<unknown>>();
const cancelInterview = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  mocks.user = { role: "hr", employee_id: MANAGER.id };
  wireQuery(useListInterviews, requestPageState([SCHEDULED, COMPLETED]));
  wireQuery(useListCandidates, requestPageState([INTERVIEWING, APPLIED]));
  wireQuery(useListJobs, requestPageState([JOB]));
  wireQuery(useListEmployees, requestPageState([MANAGER, OTHER_EMPLOYEE]));
  wireMutation(useScheduleInterview as unknown as MutateFn, scheduleInterview);
  wireMutation(useUpdateInterview as unknown as MutateFn, updateInterview);
  wireMutation(useCompleteInterview as unknown as MutateFn, completeInterview);
  wireMutation(useCancelInterview as unknown as MutateFn, cancelInterview);
});

describe("InterviewsPanel", () => {
  it("lists interviews with candidate, job, interviewer, state, rating and notes", async () => {
    renderWithClient(<InterviewsPanel />);

    expect(await screen.findAllByText("Bao Nguyen")).toHaveLength(2);
    expect(screen.getAllByText("Backend Engineer")).toHaveLength(2);
    expect(screen.getAllByText("Linh Tran")).toHaveLength(2);
    expect(
      screen.getByText("Scheduled", { selector: "[data-status='scheduled']" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Completed", { selector: "[data-status='completed']" }),
    ).toBeInTheDocument();
    expect(screen.getByText("4 / 5")).toBeInTheDocument();
    expect(screen.getByText("Strong systems thinking; follow up on scale.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reschedule interview with Bao Nguyen" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Submit feedback for Bao Nguyen" }),
    ).toBeInTheDocument();
  });

  it("schedules an interview for the selected candidate and interviewer", async () => {
    const user = userEvent.setup();
    scheduleInterview.mockResolvedValue({ data: { status: 201 } });
    renderWithClient(<InterviewsPanel />);

    await user.click(await screen.findByRole("button", { name: "Schedule interview" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Schedule interview" })).toBeInTheDocument();
    // The only interviewing candidate is preselected.
    expect(within(dialog).getByRole("combobox", { name: "Candidate" })).toHaveTextContent(
      "Bao Nguyen",
    );

    await user.click(within(dialog).getByRole("combobox", { name: "Interviewer" }));
    await user.click(await screen.findByRole("option", { name: "Omar Rahman" }));
    fireEvent.change(within(dialog).getByLabelText("Scheduled at"), {
      target: { value: "2026-11-04T10:00" },
    });
    await user.type(within(dialog).getByLabelText("Notes (optional)"), "Second round.");
    await user.click(within(dialog).getByRole("button", { name: "Schedule interview" }));

    await waitFor(() =>
      expect(scheduleInterview).toHaveBeenCalledWith({
        candidateId: INTERVIEWING.id,
        data: {
          interviewer_id: OTHER_EMPLOYEE.id,
          scheduled_at: localDateTimeToUtc("2026-11-04T10:00"),
          notes: "Second round.",
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Interview scheduled");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("surfaces a candidate stage conflict without closing the dialog", async () => {
    const user = userEvent.setup();
    scheduleInterview.mockRejectedValue(
      apiError(409, "candidate_stage_conflict", "The candidate moved"),
    );
    renderWithClient(<InterviewsPanel />);

    await user.click(await screen.findByRole("button", { name: "Schedule interview" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Interviewer" }));
    await user.click(await screen.findByRole("option", { name: "Linh Tran" }));
    fireEvent.change(within(dialog).getByLabelText("Scheduled at"), {
      target: { value: "2026-11-04T10:00" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Schedule interview" }));

    expect(
      await within(dialog).findByText(
        "The candidate isn't at the stage this action needs. Refresh and try again.",
      ),
    ).toBeInTheDocument();
    expect(dialog).toBeVisible();
  });

  it("reschedules only the changed fields", async () => {
    const user = userEvent.setup();
    updateInterview.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<InterviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Reschedule interview with Bao Nguyen" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Reschedule interview" })).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Scheduled at"), {
      target: { value: "2026-11-05T09:30" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateInterview).toHaveBeenCalledWith({
        interviewId: SCHEDULED.id,
        data: { scheduled_at: localDateTimeToUtc("2026-11-05T09:30") },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Interview updated");
  });

  it("offers feedback only to the assigned interviewer", async () => {
    const assigned = renderWithClient(<InterviewsPanel />);
    expect(
      await screen.findByRole("button", { name: "Submit feedback for Bao Nguyen" }),
    ).toBeInTheDocument();
    assigned.unmount();

    mocks.user = { role: "hr", employee_id: OTHER_EMPLOYEE.id };
    renderWithClient(<InterviewsPanel />);

    expect(
      await screen.findByRole("button", { name: "Reschedule interview with Bao Nguyen" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Submit feedback for Bao Nguyen" }),
    ).not.toBeInTheDocument();
  });

  it("submits feedback and surfaces a wrong-state refusal", async () => {
    const user = userEvent.setup();
    completeInterview.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<InterviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Submit feedback for Bao Nguyen" }),
    );
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Submit feedback" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Rating" }));
    await user.click(await screen.findByRole("option", { name: "4 — Strong" }));
    await user.type(within(dialog).getByLabelText("Feedback notes (optional)"), "Clear yes.");
    await user.click(within(dialog).getByRole("button", { name: "Submit feedback" }));

    await waitFor(() =>
      expect(completeInterview).toHaveBeenCalledWith({
        interviewId: SCHEDULED.id,
        data: { rating: 4, notes: "Clear yes." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Feedback submitted");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    completeInterview.mockRejectedValue(
      apiError(409, "interview_not_scheduled", "Already completed"),
    );
    await user.click(
      screen.getByRole("button", { name: "Submit feedback for Bao Nguyen" }),
    );
    dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Rating" }));
    await user.click(await screen.findByRole("option", { name: "4 — Strong" }));
    await user.click(within(dialog).getByRole("button", { name: "Submit feedback" }));

    expect(
      await within(dialog).findByText(
        "This interview is no longer scheduled. Refresh and try again.",
      ),
    ).toBeInTheDocument();
  });

  it("cancels a scheduled interview with an optional note", async () => {
    const user = userEvent.setup();
    cancelInterview.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<InterviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Cancel interview with Bao Nguyen" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Cancel interview" })).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Note (optional)"), "Panel changed.");
    await user.click(within(dialog).getByRole("button", { name: "Cancel interview" }));

    await waitFor(() =>
      expect(cancelInterview).toHaveBeenCalledWith({
        interviewId: SCHEDULED.id,
        data: { note: "Panel changed." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Interview cancelled");
  });

  it("renders the loading, empty and error states", async () => {
    wireQuery(useListInterviews, { isPending: true, isError: false, error: null });
    const { unmount } = renderWithClient(<InterviewsPanel />);
    expect(screen.getByText("Loading interviews…")).toBeInTheDocument();
    unmount();

    wireQuery(useListInterviews, requestPageState([]));
    const empty = renderWithClient(<InterviewsPanel />);
    expect(
      await screen.findByText("No interviews yet. Schedule one for an interviewing candidate."),
    ).toBeInTheDocument();
    empty.unmount();

    wireQuery(
      useListInterviews,
      requestPageState([], {
        isError: true,
        error: apiError(500, "server_error", "boom"),
      }),
    );
    renderWithClient(<InterviewsPanel />);
    expect(await screen.findByText("Could not load interviews.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
