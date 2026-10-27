/**
 * Goals tab — list rendering, filters, pagination, create/edit dialogs and the
 * activate/complete/cancel transitions. The generated api-client is mocked
 * wholesale; hooks, error mapping and components are the real thing.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useActivateGoal,
  useCancelGoal,
  useCompleteGoal,
  useCreateGoal,
  useGetCurrentEmployee,
  useListEmployees,
  useListGoals,
  useUpdateGoal,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { GoalsPanel } from "@/features/performance/components/GoalsPanel";
import type { Goal } from "@/features/performance/types";

import {
  apiError,
  itemState,
  renderWithClient,
  requestPageState,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

const auth = vi.hoisted(() => ({
  user: {
    role: "manager" as string,
    employee_id: "01900000-0000-7000-8000-0000000000a1" as string | null,
  },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => ({ user: auth.user }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  // Query keys used by the feature invalidation helpers.
  getListGoalsQueryKey: () => ["/api/v1/performance/goals"],
  getListReviewsQueryKey: () => ["/api/v1/performance/reviews"],
  getListSkillsQueryKey: () => ["/api/v1/performance/skills"],
  getListEmployeeSkillsQueryKey: (employeeId: string) => [
    "/api/v1/performance/employees",
    employeeId,
    "skills",
  ],
  getListReviewFeedbackQueryKey: (reviewId: string) => [
    "/api/v1/performance/reviews",
    reviewId,
    "feedback",
  ],
  // Hooks.
  useListGoals: vi.fn(),
  useListEmployees: vi.fn(),
  useGetCurrentEmployee: vi.fn(),
  useCreateGoal: vi.fn(),
  useUpdateGoal: vi.fn(),
  useActivateGoal: vi.fn(),
  useCompleteGoal: vi.fn(),
  useCancelGoal: vi.fn(),
  useListReviews: vi.fn(),
  useListSkills: vi.fn(),
}));

const TENANT_ID = "01900000-0000-7000-8000-000000000001";
const MANAGER_ID = "01900000-0000-7000-8000-0000000000a1";
const WENDY_ID = "01900000-0000-7000-8000-0000000000a2";
const DEPARTMENT_ID = "01900000-0000-7000-8000-0000000000d1";
const POSITION_ID = "01900000-0000-7000-8000-0000000000e1";
const LOCATION_ID = "01900000-0000-7000-8000-0000000000f1";

const MANAGER = {
  id: MANAGER_ID,
  employee_code: "WP-0001",
  first_name: "Morgan",
  last_name: "Lee",
  email: "morgan.lee@workpulse.test",
  department_id: DEPARTMENT_ID,
  position_id: POSITION_ID,
  location_id: LOCATION_ID,
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2024-01-08",
  terminated_at: null,
} as const;

const WENDY = {
  ...MANAGER,
  id: WENDY_ID,
  employee_code: "WP-0002",
  first_name: "Wendy",
  last_name: "Chen",
  email: "wendy.chen@workpulse.test",
  manager_id: MANAGER_ID,
} as const;

const MANAGER_FULL = {
  ...MANAGER,
  tenant_id: TENANT_ID,
  created_at: "2024-01-08T00:00:00Z",
  updated_at: "2024-01-08T00:00:00Z",
};

function goal(overrides: Partial<Goal>): Goal {
  return {
    id: "",
    employee_id: WENDY_ID,
    title: "Untitled goal",
    description: null,
    due_date: null,
    progress_pct: 0,
    status: "draft",
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

const DRAFT_GOAL = goal({
  id: "01900000-0000-7000-8000-0000000000b1",
  title: "Finish onboarding revamp",
  description: "Rewrite the first-week checklist.",
  due_date: "2026-12-31",
  progress_pct: 20,
  status: "draft",
});

const ACTIVE_GOAL = goal({
  id: "01900000-0000-7000-8000-0000000000b2",
  employee_id: MANAGER_ID,
  title: "Close the quarter",
  progress_pct: 60,
  status: "active",
});

const COMPLETED_GOAL = goal({
  id: "01900000-0000-7000-8000-0000000000b3",
  title: "Ship audit trail",
  progress_pct: 100,
  status: "completed",
  completed_at: "2026-09-30T10:00:00Z",
});

const CANCELLED_GOAL = goal({
  id: "01900000-0000-7000-8000-0000000000b4",
  title: "Retire legacy import",
  status: "cancelled",
});

const ALL_GOALS = [DRAFT_GOAL, ACTIVE_GOAL, COMPLETED_GOAL, CANCELLED_GOAL];

function goalsPageState(page: number, totalPages: number, items: Goal[] = ALL_GOALS) {
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

const createGoal = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateGoal = vi.fn<(variables: unknown) => Promise<unknown>>();
const activateGoal = vi.fn<(variables: unknown) => Promise<unknown>>();
const completeGoal = vi.fn<(variables: unknown) => Promise<unknown>>();
const cancelGoal = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { role: "manager", employee_id: MANAGER_ID };
  wireQuery(useListGoals, goalsPageState(1, 1));
  wireQuery(useListEmployees, requestPageState([MANAGER, WENDY]));
  wireQuery(useGetCurrentEmployee, itemState(MANAGER_FULL));
  wireMutation(useCreateGoal as unknown as MutateFn, createGoal);
  wireMutation(useUpdateGoal as unknown as MutateFn, updateGoal);
  wireMutation(useActivateGoal as unknown as MutateFn, activateGoal);
  wireMutation(useCompleteGoal as unknown as MutateFn, completeGoal);
  wireMutation(useCancelGoal as unknown as MutateFn, cancelGoal);
});

describe("GoalsPanel", () => {
  it("renders goals with employee names, progress, statuses and lifecycle actions", async () => {
    renderWithClient(<GoalsPanel />);

    expect(await screen.findByText("Finish onboarding revamp")).toBeInTheDocument();
    expect(screen.getByText("Close the quarter")).toBeInTheDocument();
    expect(screen.getByText("Ship audit trail")).toBeInTheDocument();
    expect(screen.getByText("Retire legacy import")).toBeInTheDocument();
    expect(screen.getAllByText("Wendy Chen").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Morgan Lee").length).toBeGreaterThan(0);

    expect(screen.getByText("20%")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("Cancelled")).toBeInTheDocument();
    expect(screen.getByText("Dec 31, 2026")).toBeInTheDocument();

    expect(
      screen.getByRole("button", { name: "Edit Finish onboarding revamp" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Activate Finish onboarding revamp" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Cancel Finish onboarding revamp" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Complete Close the quarter" })).toBeInTheDocument();

    // Terminal goals are immutable: no row actions.
    expect(screen.queryByRole("button", { name: "Edit Ship audit trail" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit Retire legacy import" }),
    ).not.toBeInTheDocument();
  });

  it("applies the status and employee filters", async () => {
    const user = userEvent.setup();
    renderWithClient(<GoalsPanel />);

    await user.click(await screen.findByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Active" }));

    await waitFor(() =>
      expect(useListGoals).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "active", page: 1 }),
      ),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by employee" }));
    await user.click(await screen.findByRole("option", { name: "Wendy Chen" }));

    await waitFor(() =>
      expect(useListGoals).toHaveBeenLastCalledWith(
        expect.objectContaining({ employee_id: WENDY_ID, page: 1 }),
      ),
    );
  });

  it("creates a draft goal with the pinned shape and invalidates the list", async () => {
    const user = userEvent.setup();
    createGoal.mockResolvedValue({ data: { status: 201 } });
    const { queryClient } = renderWithClient(<GoalsPanel />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await user.click(await screen.findByRole("button", { name: "New goal" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("combobox", { name: "Employee" }));
    await user.click(await screen.findByRole("option", { name: "Wendy Chen" }));
    await user.type(within(dialog).getByLabelText("Goal"), "Grow the platform guild");
    await user.type(
      within(dialog).getByLabelText("Description (optional)"),
      "Run a monthly forum.",
    );
    fireEvent.change(within(dialog).getByLabelText("Due date (optional)"), {
      target: { value: "2026-12-31" },
    });
    fireEvent.change(within(dialog).getByLabelText("Progress"), {
      target: { value: "40" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Create goal" }));

    await waitFor(() =>
      expect(createGoal).toHaveBeenCalledWith({
        data: {
          title: "Grow the platform guild",
          description: "Run a monthly forum.",
          due_date: "2026-12-31",
          progress_pct: 40,
          employee_id: WENDY_ID,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Goal created");
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["/api/v1/performance/goals"],
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("edits a goal and keeps the input when a stale row refuses the change", async () => {
    const user = userEvent.setup();
    updateGoal.mockRejectedValue(apiError(409, "goal_terminal", "Goal is completed or cancelled"));
    renderWithClient(<GoalsPanel />);

    await user.click(await screen.findByRole("button", { name: "Edit Close the quarter" }));
    const dialog = await screen.findByRole("dialog");
    const title = within(dialog).getByLabelText("Goal");
    expect(title).toHaveValue("Close the quarter");

    await user.clear(title);
    await user.type(title, "Close the quarter strongly");
    fireEvent.change(within(dialog).getByLabelText("Progress"), {
      target: { value: "75" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateGoal).toHaveBeenCalledWith({
        id: ACTIVE_GOAL.id,
        data: {
          title: "Close the quarter strongly",
          description: null,
          due_date: null,
          progress_pct: 75,
        },
      }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This goal is completed or cancelled and can no longer change.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Goal")).toHaveValue("Close the quarter strongly");
  });

  it("activates a draft goal after confirmation", async () => {
    const user = userEvent.setup();
    activateGoal.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<GoalsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Activate Finish onboarding revamp" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Activate goal" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Activate" }));

    await waitFor(() => expect(activateGoal).toHaveBeenCalledWith({ id: DRAFT_GOAL.id }));
    expect(toast.success).toHaveBeenCalledWith("Goal activated");
  });

  it("completes an active goal and renders a stale-state refusal in place", async () => {
    const user = userEvent.setup();
    completeGoal.mockRejectedValue(
      apiError(409, "goal_not_active", "Only an active goal can complete"),
    );
    renderWithClient(<GoalsPanel />);

    await user.click(await screen.findByRole("button", { name: "Complete Close the quarter" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Complete goal" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Complete" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Only an active goal can be completed. Refresh the list and try again.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(completeGoal).toHaveBeenCalledWith({ id: ACTIVE_GOAL.id });
  });

  it("cancels a goal after confirmation", async () => {
    const user = userEvent.setup();
    cancelGoal.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<GoalsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Cancel Finish onboarding revamp" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Cancel goal" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel goal" }));

    await waitFor(() => expect(cancelGoal).toHaveBeenCalledWith({ id: DRAFT_GOAL.id }));
    expect(toast.success).toHaveBeenCalledWith("Goal cancelled");
  });

  it("returns to the first page before opening the create dialog", async () => {
    const user = userEvent.setup();
    wireQuery(useListGoals, goalsPageState(1, 2));
    renderWithClient(<GoalsPanel />);

    await user.click(await screen.findByRole("button", { name: "Next page of goals" }));
    await waitFor(() =>
      expect(useListGoals).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
    );

    await user.click(screen.getByRole("button", { name: "New goal" }));
    await waitFor(() =>
      expect(useListGoals).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })),
    );
  });

  it("offers a way back from an out-of-range page", async () => {
    const user = userEvent.setup();
    wireQuery(useListGoals, goalsPageState(1, 2));
    renderWithClient(<GoalsPanel />);

    const next = await screen.findByRole("button", { name: "Next page of goals" });
    wireQuery(useListGoals, goalsPageState(2, 1, []));
    await user.click(next);

    await waitFor(() =>
      expect(useListGoals).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
    );
    await user.click(await screen.findByRole("button", { name: "Back to first page" }));
    await waitFor(() =>
      expect(useListGoals).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })),
    );
  });

  it("lets an Employee create an own goal without exposing team controls", async () => {
    const user = userEvent.setup();
    auth.user = { role: "employee", employee_id: WENDY_ID };
    wireQuery(useGetCurrentEmployee, itemState({ ...WENDY, tenant_id: TENANT_ID }));
    createGoal.mockResolvedValue({ data: { status: 201 } });
    renderWithClient(<GoalsPanel />);

    expect(screen.queryByRole("combobox", { name: "Filter by employee" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Employee" })).not.toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "New goal" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("combobox", { name: "Employee" })).not.toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Goal"), "Own the release notes");
    await user.click(within(dialog).getByRole("button", { name: "Create goal" }));

    await waitFor(() =>
      expect(createGoal).toHaveBeenCalledWith({
        data: {
          title: "Own the release notes",
          description: null,
          due_date: null,
          progress_pct: 0,
        },
      }),
    );
  });

  it("shows Finance their goals read-only", async () => {
    auth.user = { role: "finance", employee_id: WENDY_ID };
    wireQuery(useGetCurrentEmployee, itemState({ ...WENDY, tenant_id: TENANT_ID }));
    renderWithClient(<GoalsPanel />);

    expect(await screen.findByText("Finish onboarding revamp")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New goal" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit Finish onboarding revamp" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Activate Finish onboarding revamp" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Filter by employee" })).not.toBeInTheDocument();
  });

  it("shows the loading state while the list is fetched", () => {
    wireQuery(useListGoals, {
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderWithClient(<GoalsPanel />);

    expect(screen.getByText("Loading goals…")).toBeInTheDocument();
  });

  it("shows the empty state when no goal matches", async () => {
    wireQuery(useListGoals, goalsPageState(1, 1, []));
    renderWithClient(<GoalsPanel />);

    expect(
      await screen.findByText("No goals yet. Create a goal to start tracking progress."),
    ).toBeInTheDocument();
  });

  it("shows the denied state when the API refuses the list", async () => {
    wireQuery(useListGoals, {
      data: undefined,
      isPending: false,
      isError: true,
      error: apiError(403, "performance_denied", "Not allowed"),
      refetch: vi.fn(),
    });
    renderWithClient(<GoalsPanel />);

    expect(
      await screen.findByText("You don't have permission to view these goals."),
    ).toBeInTheDocument();
  });

  it("shows a retryable error state for a failed read", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    wireQuery(useListGoals, {
      data: undefined,
      isPending: false,
      isError: true,
      error: new Error("boom"),
      refetch,
    });
    renderWithClient(<GoalsPanel />);

    expect(await screen.findByText("Could not load goals.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });
});
