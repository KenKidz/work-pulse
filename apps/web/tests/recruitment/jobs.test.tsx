/**
 * Jobs tab — list rendering with resolved organization references, filters
 * and debounce, create/edit dialogs, open/close transitions and the loading,
 * empty and error states. The generated api-client is mocked wholesale.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getListDepartmentsQueryOptions,
  useCloseJob,
  useCreateJob,
  useListDepartments,
  useListJobs,
  useListLocations,
  useListPositions,
  useOpenJob,
  useUpdateJob,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { JobsTable } from "@/features/recruitment/components/JobsTable";
import type { Job } from "@/features/recruitment/types";

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
  // Hooks.
  useListJobs: vi.fn(),
  useCreateJob: vi.fn(),
  useUpdateJob: vi.fn(),
  useOpenJob: vi.fn(),
  useCloseJob: vi.fn(),
  useListDepartments: vi.fn(),
  useListPositions: vi.fn(),
  useListLocations: vi.fn(),
}));

const TENANT_ID = "01900000-0000-7000-8000-000000000001";

const DEPARTMENT = {
  id: "01900000-0000-7000-8000-000000000d01",
  tenant_id: TENANT_ID,
  name: "Engineering",
  parent_department_id: null,
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const POSITION = {
  id: "01900000-0000-7000-8000-000000000201",
  tenant_id: TENANT_ID,
  title: "Backend Engineer",
  description: null,
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const LOCATION = {
  id: "01900000-0000-7000-8000-000000000301",
  tenant_id: TENANT_ID,
  name: "Ho Chi Minh Office",
  address: null,
  timezone: "Asia/Ho_Chi_Minh",
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function job(overrides: Partial<Job>): Job {
  return {
    id: "",
    tenant_id: TENANT_ID,
    title: "Untitled role",
    description: null,
    department_id: DEPARTMENT.id,
    position_id: POSITION.id,
    location_id: LOCATION.id,
    openings: 1,
    status: "draft",
    created_by: "01900000-0000-7000-8000-000000000014",
    opened_at: null,
    closed_at: null,
    created_at: "2026-10-20T09:00:00Z",
    updated_at: "2026-10-20T09:00:00Z",
    ...overrides,
  };
}

const DRAFT_JOB = job({
  id: "01900000-0000-7000-8000-0000000j0001",
  title: "Backend Engineer",
  description: "Own the workforce API.",
  openings: 2,
  status: "draft",
});

const OPEN_JOB = job({
  id: "01900000-0000-7000-8000-0000000j0002",
  title: "Platform Engineer",
  openings: 1,
  status: "open",
  opened_at: "2026-10-21T09:00:00Z",
});

const CLOSED_JOB = job({
  id: "01900000-0000-7000-8000-0000000j0003",
  title: "Legacy Role",
  status: "closed",
  opened_at: "2026-09-01T09:00:00Z",
  closed_at: "2026-09-30T09:00:00Z",
});

const OPERATIONS = {
  ...DEPARTMENT,
  id: "01900000-0000-7000-8000-000000000d02",
  name: "Platform",
};

const OPERATIONS_JOB = job({
  id: "01900000-0000-7000-8000-0000000j0004",
  title: "Site Reliability Engineer",
  department_id: OPERATIONS.id,
});

function jobsPageState(
  page: number,
  totalPages: number,
  items: Job[] = [DRAFT_JOB, OPEN_JOB, CLOSED_JOB],
) {
  return {
    data: {
      data: {
        items,
        page: {
          page,
          page_size: 20,
          total_items: totalPages * 20,
          total_pages: totalPages,
        },
      },
      status: 200,
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

const createJob = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateJob = vi.fn<(variables: unknown) => Promise<unknown>>();
const openJob = vi.fn<(variables: unknown) => Promise<unknown>>();
const closeJob = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useListJobs, requestPageState([DRAFT_JOB, OPEN_JOB, CLOSED_JOB]));
  wireQuery(useListDepartments, requestPageState([DEPARTMENT]));
  wireQuery(useListPositions, requestPageState([POSITION]));
  wireQuery(useListLocations, requestPageState([LOCATION]));
  wireMutation(useCreateJob as unknown as MutateFn, createJob);
  wireMutation(useUpdateJob as unknown as MutateFn, updateJob);
  wireMutation(useOpenJob as unknown as MutateFn, openJob);
  wireMutation(useCloseJob as unknown as MutateFn, closeJob);
});

describe("JobsTable", () => {
  it("renders jobs with resolved organization names, statuses and lifecycle actions", async () => {
    renderWithClient(<JobsTable />);

    const backendNames = await screen.findAllByText("Backend Engineer");
    expect(backendNames.length).toBeGreaterThan(1); // job title + the position column
    expect(screen.getByText("Platform Engineer")).toBeInTheDocument();
    expect(screen.getByText("Legacy Role")).toBeInTheDocument();
    expect(screen.getAllByText("Engineering").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Backend Engineer").length).toBeGreaterThan(1);
    expect(screen.getAllByText("Ho Chi Minh Office").length).toBeGreaterThan(0);
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("Open", { selector: "[data-status='open']" })).toBeInTheDocument();
    expect(screen.getByText("Closed")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Edit Backend Engineer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Backend Engineer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Platform Engineer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close Platform Engineer" })).toBeInTheDocument();
    // Closed jobs are terminal: no edit/open/close actions.
    expect(screen.queryByRole("button", { name: "Edit Legacy Role" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Legacy Role$/ })).not.toBeInTheDocument();
  });

  it("debounces the search and applies the status filter", async () => {
    const user = userEvent.setup();
    renderWithClient(<JobsTable />);

    await user.type(await screen.findByLabelText("Search jobs"), "platform");

    await waitFor(() =>
      expect(useListJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "platform", page: 1 }),
      ),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Open" }));

    await waitFor(() =>
      expect(useListJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "open", q: "platform", page: 1 }),
      ),
    );
  });

  it("creates a draft job with the pinned JobCreate shape", async () => {
    const user = userEvent.setup();
    createJob.mockResolvedValue({ data: { status: 201 } });
    renderWithClient(<JobsTable />);

    await user.click(await screen.findByRole("button", { name: "New job" }));
    expect(screen.getByRole("heading", { name: "New job" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Title"), "Staff Engineer");
    const openings = screen.getByLabelText("Openings");
    // jsdom's userEvent.clear does not reset a controlled number input.
    fireEvent.change(openings, { target: { value: "2" } });

    await user.click(screen.getByRole("combobox", { name: "Department" }));
    await user.click(await screen.findByRole("option", { name: "Engineering" }));
    await user.click(screen.getByRole("combobox", { name: "Position" }));
    await user.click(await screen.findByRole("option", { name: "Backend Engineer" }));
    await user.click(screen.getByRole("combobox", { name: "Location" }));
    await user.click(await screen.findByRole("option", { name: "Ho Chi Minh Office" }));

    await user.click(screen.getByRole("button", { name: "Create job" }));

    await waitFor(() =>
      expect(createJob).toHaveBeenCalledWith({
        data: {
          title: "Staff Engineer",
          description: null,
          department_id: DEPARTMENT.id,
          position_id: POSITION.id,
          location_id: LOCATION.id,
          openings: 2,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Job created");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("edits a job with the pinned JobUpdate shape", async () => {
    const user = userEvent.setup();
    updateJob.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<JobsTable />);

    await user.click(await screen.findByRole("button", { name: "Edit Backend Engineer" }));
    const title = screen.getByLabelText("Title");
    expect(title).toHaveValue("Backend Engineer");
    expect(screen.getByLabelText("Description (optional)")).toHaveValue("Own the workforce API.");

    await user.clear(title);
    await user.type(title, "Senior Backend Engineer");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateJob).toHaveBeenCalledWith({
        jobId: DRAFT_JOB.id,
        data: {
          title: "Senior Backend Engineer",
          description: "Own the workforce API.",
          department_id: DEPARTMENT.id,
          position_id: POSITION.id,
          location_id: LOCATION.id,
          openings: 2,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Job updated");
  });

  it("starts a clean create form after the dialog is cancelled", async () => {
    const user = userEvent.setup();
    renderWithClient(<JobsTable />);

    await user.click(await screen.findByRole("button", { name: "New job" }));
    await user.type(screen.getByLabelText("Title"), "Discarded draft");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "New job" }));
    expect(await screen.findByLabelText("Title")).toHaveValue("");
  });

  it("returns to the first page before opening the create dialog", async () => {
    const user = userEvent.setup();
    wireQuery(useListJobs, jobsPageState(1, 3));
    renderWithClient(<JobsTable />);

    await user.click(await screen.findByRole("button", { name: "Next page of jobs" }));
    await waitFor(() =>
      expect(useListJobs).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
    );

    await user.click(screen.getByRole("button", { name: "New job" }));
    await waitFor(() =>
      expect(useListJobs).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })),
    );
  });

  it("offers a way back from an out-of-range page", async () => {
    const user = userEvent.setup();
    wireQuery(useListJobs, jobsPageState(1, 2));
    renderWithClient(<JobsTable />);

    const next = await screen.findByRole("button", { name: "Next page of jobs" });
    // The page-2 response is empty and reports a single total page.
    wireQuery(useListJobs, jobsPageState(2, 1, []));
    await user.click(next);

    await waitFor(() =>
      expect(useListJobs).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
    );
    await user.click(await screen.findByRole("button", { name: "Back to first page" }));
    await waitFor(() =>
      expect(useListJobs).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })),
    );
  });

  it("resolves organization references from later directory pages", async () => {
    wireQuery(useListJobs, requestPageState([OPERATIONS_JOB]));
    wireQuery(useListDepartments, {
      data: {
        data: {
          items: [DEPARTMENT],
          page: { page: 1, page_size: 100, total_items: 101, total_pages: 2 },
        },
        status: 200,
      },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    const departmentsOptions = getListDepartmentsQueryOptions as unknown as {
      mockImplementation: (implementation: (params?: { page?: number }) => unknown) => void;
    };
    departmentsOptions.mockImplementation((params) => ({
      queryKey: ["/api/v1/departments", params],
      queryFn: async () => ({
        status: 200,
        data: {
          items: params?.page === 2 ? [OPERATIONS] : [],
          page: {
            page: params?.page ?? 1,
            page_size: 100,
            total_items: 101,
            total_pages: 2,
          },
        },
      }),
    }));

    renderWithClient(<JobsTable />);

    expect(await screen.findByText("Platform")).toBeInTheDocument();
  });

  it("opens a draft job after confirmation", async () => {
    const user = userEvent.setup();
    openJob.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<JobsTable />);

    await user.click(await screen.findByRole("button", { name: "Open Backend Engineer" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Open job" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Open job" }));

    await waitFor(() => expect(openJob).toHaveBeenCalledWith({ jobId: DRAFT_JOB.id }));
    expect(toast.success).toHaveBeenCalledWith("Job opened");
  });

  it("closes an open job after confirmation and keeps a failure visible", async () => {
    const user = userEvent.setup();
    closeJob.mockRejectedValue(apiError(409, "job_not_open", "Job is already closed"));
    renderWithClient(<JobsTable />);

    await user.click(await screen.findByRole("button", { name: "Close Platform Engineer" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Close job" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close job" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This job's state no longer allows that action. Refresh the list and try again.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(closeJob).toHaveBeenCalledWith({ jobId: OPEN_JOB.id });
  });

  it("shows the loading state while the list is fetched", () => {
    wireQuery(useListJobs, {
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderWithClient(<JobsTable />);

    expect(screen.getByText("Loading jobs…")).toBeInTheDocument();
  });

  it("shows the error state and retries", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    wireQuery(useListJobs, {
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error("boom"),
      refetch,
    });
    renderWithClient(<JobsTable />);

    expect(await screen.findByText("Could not load jobs.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("shows the empty state when no job matches", async () => {
    wireQuery(useListJobs, requestPageState([]));
    renderWithClient(<JobsTable />);

    expect(
      await screen.findByText("No jobs yet. Create a job to start a hiring pipeline."),
    ).toBeInTheDocument();
  });
});
