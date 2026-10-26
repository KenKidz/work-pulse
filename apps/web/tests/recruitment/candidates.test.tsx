/**
 * Candidates tab — pipeline rendering, filters, intake/edit, stage moves,
 * hire (including the seam failure path) and the loading/empty/error/denied
 * states. The generated api-client is mocked wholesale.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useAdvanceCandidate,
  useCreateCandidate,
  useHireCandidate,
  useListCandidates,
  useListEmployees,
  useListJobs,
  useRejectCandidate,
  useUpdateCandidate,
  useWithdrawCandidate,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { todayISO } from "@/features/recruitment/dates";
import { CandidatesPanel } from "@/features/recruitment/components/CandidatesPanel";
import type { Candidate, EmployeeSummary, Job } from "@/features/recruitment/types";

import {
  apiError,
  renderWithClient,
  requestPageState,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

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
  getListDepartmentsQueryOptions: vi.fn(),
  getListPositionsQueryOptions: vi.fn(),
  getListLocationsQueryOptions: vi.fn(),
  // Hooks used by this surface and the shared recruitment module.
  useListCandidates: vi.fn(),
  useCreateCandidate: vi.fn(),
  useUpdateCandidate: vi.fn(),
  useAdvanceCandidate: vi.fn(),
  useRejectCandidate: vi.fn(),
  useWithdrawCandidate: vi.fn(),
  useHireCandidate: vi.fn(),
  useListJobs: vi.fn(),
  useListEmployees: vi.fn(),
  useListDepartments: vi.fn(),
  useListPositions: vi.fn(),
  useListLocations: vi.fn(),
  useCreateJob: vi.fn(),
  useUpdateJob: vi.fn(),
  useOpenJob: vi.fn(),
  useCloseJob: vi.fn(),
}));

const TENANT_ID = "01900000-0000-7000-8000-000000000001";

const OPEN_JOB: Job = {
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

const CLOSED_JOB: Job = {
  ...OPEN_JOB,
  id: "01900000-0000-7000-8000-0000000000b2",
  title: "Legacy Role",
  status: "closed",
  opened_at: "2026-09-01T09:00:00Z",
  closed_at: "2026-09-30T09:00:00Z",
};

const MANAGER: EmployeeSummary = {
  id: "01900000-0000-7000-8000-0000000000a9",
  employee_code: "EMP-00009",
  first_name: "Linh",
  last_name: "Tran",
  email: "linh.tran@example.test",
  department_id: OPEN_JOB.department_id,
  position_id: OPEN_JOB.position_id,
  location_id: OPEN_JOB.location_id,
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2024-01-15",
  terminated_at: null,
};

function candidate(overrides: Partial<Candidate>): Candidate {
  return {
    id: "",
    tenant_id: TENANT_ID,
    job_id: OPEN_JOB.id,
    first_name: "Amara",
    last_name: "Okafor",
    email: "amara.okafor@example.test",
    phone: null,
    source: "referral",
    notes: null,
    status: "applied",
    stage_note: null,
    rejected_from: null,
    hired_at: null,
    employee_id: null,
    created_at: "2026-10-22T09:00:00Z",
    updated_at: "2026-10-22T09:00:00Z",
    ...overrides,
  };
}

const APPLIED = candidate({ id: "01900000-0000-7000-8000-0000000000c1" });

const INTERVIEWING = candidate({
  id: "01900000-0000-7000-8000-0000000000c2",
  first_name: "Bao",
  last_name: "Nguyen",
  email: "bao.nguyen@example.test",
  phone: "+84 90 123 4567",
  source: "job_board",
  status: "interviewing",
});

const OFFERED = candidate({
  id: "01900000-0000-7000-8000-0000000000c3",
  first_name: "Chidi",
  last_name: "Eze",
  email: "chidi.eze@example.test",
  source: "agency",
  notes: "Strong systems background.",
  status: "offered",
});

const HIRED = candidate({
  id: "01900000-0000-7000-8000-0000000000c4",
  first_name: "Dalia",
  last_name: "Haddad",
  email: "dalia.haddad@example.test",
  status: "hired",
  hired_at: "2026-10-24T09:00:00Z",
  employee_id: "01900000-0000-7000-8000-0000000000e9",
});

const REJECTED = candidate({
  id: "01900000-0000-7000-8000-0000000000c5",
  job_id: CLOSED_JOB.id,
  first_name: "Emeka",
  last_name: "Obi",
  email: "emeka.obi@example.test",
  source: "other",
  status: "rejected",
  rejected_from: "applied",
});

const OFFERED_CLOSED = candidate({
  id: "01900000-0000-7000-8000-0000000000c6",
  job_id: CLOSED_JOB.id,
  first_name: "Farah",
  last_name: "Mansour",
  email: "farah.mansour@example.test",
  status: "offered",
});

const createCandidate = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateCandidate = vi.fn<(variables: unknown) => Promise<unknown>>();
const advanceCandidate = vi.fn<(variables: unknown) => Promise<unknown>>();
const rejectCandidate = vi.fn<(variables: unknown) => Promise<unknown>>();
const withdrawCandidate = vi.fn<(variables: unknown) => Promise<unknown>>();
const hireCandidate = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useListCandidates, requestPageState([APPLIED, INTERVIEWING, OFFERED, HIRED, REJECTED]));
  wireQuery(useListJobs, requestPageState([OPEN_JOB, CLOSED_JOB]));
  wireQuery(useListEmployees, requestPageState([MANAGER]));
  wireMutation(useCreateCandidate as unknown as MutateFn, createCandidate);
  wireMutation(useUpdateCandidate as unknown as MutateFn, updateCandidate);
  wireMutation(useAdvanceCandidate as unknown as MutateFn, advanceCandidate);
  wireMutation(useRejectCandidate as unknown as MutateFn, rejectCandidate);
  wireMutation(useWithdrawCandidate as unknown as MutateFn, withdrawCandidate);
  wireMutation(useHireCandidate as unknown as MutateFn, hireCandidate);
});

describe("CandidatesPanel", () => {
  it("renders the pipeline with job titles, sources, statuses and stage actions", async () => {
    renderWithClient(<CandidatesPanel />);

    expect(await screen.findByText("Amara Okafor")).toBeInTheDocument();
    expect(screen.getByText("Bao Nguyen")).toBeInTheDocument();
    expect(screen.getByText("Chidi Eze")).toBeInTheDocument();
    expect(screen.getByText("Dalia Haddad")).toBeInTheDocument();
    expect(screen.getByText("Emeka Obi")).toBeInTheDocument();

    expect(screen.getAllByText("Backend Engineer").length).toBeGreaterThan(0);
    expect(screen.getByText("Legacy Role")).toBeInTheDocument();
    expect(screen.getAllByText("Referral").length).toBeGreaterThan(0);
    expect(screen.getByText("Job board")).toBeInTheDocument();
    expect(screen.getByText("Agency")).toBeInTheDocument();
    expect(
      screen.getByText("Applied", { selector: "[data-status='applied']" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Offered", { selector: "[data-status='offered']" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Hired", { selector: "[data-status='hired']" })).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Advance Amara Okafor" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject Amara Okafor" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw Amara Okafor" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hire Amara Okafor" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hire Chidi Eze" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Advance Chidi Eze" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Advance Dalia Haddad" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View employee Dalia Haddad" })).toHaveAttribute(
      "href",
      `/employees/${HIRED.employee_id}`,
    );
  });

  it("does not offer hire on a closed job", async () => {
    wireQuery(useListCandidates, requestPageState([OFFERED_CLOSED]));
    renderWithClient(<CandidatesPanel />);

    expect(await screen.findByText("Farah Mansour")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hire Farah Mansour" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject Farah Mansour" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw Farah Mansour" })).toBeInTheDocument();
  });

  it("filters by debounced name, job and status", async () => {
    const user = userEvent.setup();
    renderWithClient(<CandidatesPanel />);

    await user.type(await screen.findByLabelText("Search candidates"), "bao");
    await waitFor(() =>
      expect(useListCandidates).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "bao", page: 1 }),
      ),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by job" }));
    await user.click(await screen.findByRole("option", { name: "Backend Engineer" }));
    await waitFor(() =>
      expect(useListCandidates).toHaveBeenLastCalledWith(
        expect.objectContaining({ job_id: OPEN_JOB.id, q: "bao", page: 1 }),
      ),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Interviewing" }));
    await waitFor(() =>
      expect(useListCandidates).toHaveBeenLastCalledWith(
        expect.objectContaining({
          job_id: OPEN_JOB.id,
          status: "interviewing",
          q: "bao",
          page: 1,
        }),
      ),
    );
  });

  it("creates a candidate on an open job with the pinned intake shape", async () => {
    const user = userEvent.setup();
    createCandidate.mockResolvedValue({ data: { status: 201 } });
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "New candidate" }));
    expect(screen.getByRole("heading", { name: "New candidate" })).toBeInTheDocument();
    // The only open job is preselected; the closed job is not offered.
    expect(screen.getByRole("combobox", { name: "Job" })).toHaveTextContent("Backend Engineer");
    await user.click(screen.getByRole("combobox", { name: "Job" }));
    expect(await screen.findByRole("option", { name: "Backend Engineer" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Legacy Role" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.type(screen.getByLabelText("First name"), "Mina");
    await user.type(screen.getByLabelText("Last name"), "Park");
    await user.type(screen.getByLabelText("Email"), "mina.park@example.test");
    await user.type(screen.getByLabelText("Phone (optional)"), "+84 90 000 0000");
    await user.click(screen.getByRole("combobox", { name: "Source" }));
    await user.click(await screen.findByRole("option", { name: "Job board" }));
    await user.type(screen.getByLabelText("Notes (optional)"), "Referred by the platform team.");
    await user.click(screen.getByRole("button", { name: "Add candidate" }));

    await waitFor(() =>
      expect(createCandidate).toHaveBeenCalledWith({
        jobId: OPEN_JOB.id,
        data: {
          first_name: "Mina",
          last_name: "Park",
          email: "mina.park@example.test",
          phone: "+84 90 000 0000",
          source: "job_board",
          notes: "Referred by the platform team.",
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Candidate added");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("edits a candidate's contact fields", async () => {
    const user = userEvent.setup();
    updateCandidate.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "Edit Amara Okafor" }));
    expect(screen.getByRole("heading", { name: "Edit candidate" })).toBeInTheDocument();
    expect(screen.getByText("Backend Engineer", { selector: "p" })).toBeInTheDocument();

    const email = screen.getByLabelText("Email");
    expect(email).toHaveValue(APPLIED.email);
    await user.clear(email);
    await user.type(email, "amara.okafor@workpulse.test");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateCandidate).toHaveBeenCalledWith({
        candidateId: APPLIED.id,
        data: {
          first_name: "Amara",
          last_name: "Okafor",
          email: "amara.okafor@workpulse.test",
          phone: null,
          source: "referral",
          notes: null,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Candidate updated");
  });

  it("advances one stage and surfaces a stage conflict without changing the row", async () => {
    const user = userEvent.setup();
    advanceCandidate.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "Advance Amara Okafor" }));
    await waitFor(() => expect(advanceCandidate).toHaveBeenCalledWith({ candidateId: APPLIED.id }));
    expect(toast.success).toHaveBeenCalledWith("Candidate advanced");

    advanceCandidate.mockRejectedValue(
      apiError(409, "candidate_stage_conflict", "The candidate already moved"),
    );
    await user.click(screen.getByRole("button", { name: "Advance Amara Okafor" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "The candidate isn't at the stage this action needs. Refresh and try again.",
      ),
    );
    expect(
      screen.getByText("Applied", { selector: "[data-status='applied']" }),
    ).toBeInTheDocument();
  });

  it("rejects a candidate with a note", async () => {
    const user = userEvent.setup();
    rejectCandidate.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "Reject Bao Nguyen" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Reject candidate" })).toBeInTheDocument();
    await user.type(
      within(dialog).getByLabelText("Note (optional)"),
      "Offer went to another candidate.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Reject candidate" }));

    await waitFor(() =>
      expect(rejectCandidate).toHaveBeenCalledWith({
        candidateId: INTERVIEWING.id,
        data: { note: "Offer went to another candidate." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Candidate rejected");
  });

  it("withdraws a candidate with no note", async () => {
    const user = userEvent.setup();
    withdrawCandidate.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "Withdraw Chidi Eze" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Withdraw candidate" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Withdraw candidate" }));

    await waitFor(() =>
      expect(withdrawCandidate).toHaveBeenCalledWith({
        candidateId: OFFERED.id,
        data: { note: null },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Candidate withdrawn");
  });

  it("hires an offered candidate with terms and contact overrides", async () => {
    const user = userEvent.setup();
    hireCandidate.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "Hire Chidi Eze" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Hire candidate" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Hire date")).toHaveValue(todayISO());

    await user.click(within(dialog).getByRole("combobox", { name: "Employment type" }));
    await user.click(await screen.findByRole("option", { name: "Part-time" }));
    await user.click(within(dialog).getByRole("combobox", { name: "Manager (optional)" }));
    await user.click(await screen.findByRole("option", { name: "Linh Tran" }));
    await user.type(within(dialog).getByLabelText("Employee code (optional)"), "EMP-9001");
    await user.type(within(dialog).getByLabelText("First name"), "Chidi");
    await user.click(within(dialog).getByRole("button", { name: "Hire candidate" }));

    await waitFor(() => expect(hireCandidate).toHaveBeenCalledTimes(1));
    const variables = hireCandidate.mock.calls[0]?.[0] as {
      candidateId: string;
      data: Record<string, unknown>;
    };
    expect(variables.candidateId).toBe(OFFERED.id);
    expect(variables.data).toMatchObject({
      employment_type: "part_time",
      hired_at: todayISO(),
      manager_id: MANAGER.id,
      employee_code: "EMP-9001",
      first_name: "Chidi",
    });
    expect(variables.data.last_name).toBeUndefined();
    expect(toast.success).toHaveBeenCalledWith("Candidate hired");
  });

  it("keeps a failed hire visible and leaves the candidate offered", async () => {
    const user = userEvent.setup();
    hireCandidate.mockRejectedValue(
      apiError(409, "employee_email_taken", "Employee email already exists"),
    );
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "Hire Chidi Eze" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Hire candidate" }));

    // The email override is the field the seam failure points at, so the
    // message renders under it and the dialog stays open.
    expect(
      await within(dialog).findByText(
        "An employee already uses this email. Override the email or update the existing record.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText("Offered", { selector: "[data-status='offered']" }),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith(
      "An employee already uses this email. Override the email or update the existing record.",
    );
  });

  it("keeps a rejected-candidate conflict in the decision dialog", async () => {
    const user = userEvent.setup();
    rejectCandidate.mockRejectedValue(
      apiError(409, "candidate_not_open", "Candidate is already closed"),
    );
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("button", { name: "Reject Amara Okafor" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Reject candidate" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This candidate is already closed and can't be rejected or withdrawn again.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows the loading state while the pipeline is fetched", () => {
    wireQuery(useListCandidates, {
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderWithClient(<CandidatesPanel />);

    expect(screen.getByText("Loading candidates…")).toBeInTheDocument();
  });

  it("shows the error state and retries", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    wireQuery(useListCandidates, {
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error("boom"),
      refetch,
    });
    renderWithClient(<CandidatesPanel />);

    expect(await screen.findByText("Could not load candidates.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("shows the denied state for a forbidden read", async () => {
    wireQuery(useListCandidates, {
      data: undefined,
      isPending: false,
      isError: true,
      error: apiError(403, "recruitment_denied", "Denied"),
      refetch: vi.fn(),
    });
    renderWithClient(<CandidatesPanel />);

    expect(
      await screen.findByText("You don't have permission to manage recruitment."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("shows the empty state when no candidate matches", async () => {
    wireQuery(useListCandidates, requestPageState([]));
    renderWithClient(<CandidatesPanel />);

    expect(
      await screen.findByText(
        "No candidates yet. Add a candidate to an open job to start the pipeline.",
      ),
    ).toBeInTheDocument();
  });

  it("explains an empty pipeline for the filtered job", async () => {
    const user = userEvent.setup();
    wireQuery(useListCandidates, requestPageState([]));
    renderWithClient(<CandidatesPanel />);

    await user.click(await screen.findByRole("combobox", { name: "Filter by job" }));
    await user.click(await screen.findByRole("option", { name: "Backend Engineer" }));

    expect(
      await screen.findByText(
        "No candidates for this job yet. Add a candidate to start the pipeline.",
      ),
    ).toBeInTheDocument();
  });
});
