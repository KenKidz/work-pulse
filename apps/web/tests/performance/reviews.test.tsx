/**
 * Reviews tab — the author workspace (create with peer nomination, draft edit,
 * complete/cancel) and the subject's completed-only "My reviews". The generated
 * api-client is mocked wholesale; hooks, error mapping and components are the
 * real thing.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useCancelReview,
  useCompleteReview,
  useCreateReview,
  useGetCurrentEmployee,
  useListEmployees,
  useListReviewFeedback,
  useListReviews,
  useSubmitReviewFeedback,
  useUpdateReview,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { ReviewsPanel } from "@/features/performance/components/ReviewsPanel";
import type { Review } from "@/features/performance/types";

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

vi.mock("@workpulse/api-client", async () =>
  (await import("./api-client-mock")).createPerformanceApiClientMock(),
);

const MANAGER_ID = "01900000-0000-7000-8000-0000000000a1";
const WENDY_ID = "01900000-0000-7000-8000-0000000000a2";
const PEER_ID = "01900000-0000-7000-8000-0000000000a3";
const TENANT_ID = "01900000-0000-7000-8000-000000000001";
const DEPARTMENT_ID = "01900000-0000-7000-8000-0000000000d1";
const POSITION_ID = "01900000-0000-7000-8000-0000000000e1";
const LOCATION_ID = "01900000-0000-7000-8000-0000000000f1";

const BASE_EMPLOYEE = {
  employee_code: "WP-0001",
  email: "person@workpulse.test",
  department_id: DEPARTMENT_ID,
  position_id: POSITION_ID,
  location_id: LOCATION_ID,
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2024-01-08",
  terminated_at: null,
} as const;

const MANAGER = {
  ...BASE_EMPLOYEE,
  id: MANAGER_ID,
  first_name: "Morgan",
  last_name: "Lee",
  email: "morgan.lee@workpulse.test",
} as const;

const WENDY = {
  ...BASE_EMPLOYEE,
  id: WENDY_ID,
  employee_code: "WP-0002",
  first_name: "Wendy",
  last_name: "Chen",
  email: "wendy.chen@workpulse.test",
  manager_id: MANAGER_ID,
} as const;

const PEER = {
  ...BASE_EMPLOYEE,
  id: PEER_ID,
  employee_code: "WP-0003",
  first_name: "Priya",
  last_name: "Nair",
  email: "priya.nair@workpulse.test",
  manager_id: MANAGER_ID,
} as const;

const MANAGER_FULL = {
  ...MANAGER,
  tenant_id: TENANT_ID,
  created_at: "2024-01-08T00:00:00Z",
  updated_at: "2024-01-08T00:00:00Z",
};

function review(overrides: Partial<Review>): Review {
  return {
    id: "",
    employee_id: WENDY_ID,
    period: "2026-H1",
    rating: null,
    strengths: null,
    improvements: null,
    status: "draft",
    feedback_provider_ids: [PEER_ID],
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

const DRAFT_REVIEW = review({
  id: "01900000-0000-7000-8000-0000000000b1",
  period: "2026-Q3",
});

const COMPLETED_REVIEW = review({
  id: "01900000-0000-7000-8000-0000000000b2",
  period: "2026-H1",
  rating: 4,
  strengths: "Owned the migration end to end.",
  improvements: "Bring the team into design reviews earlier.",
  status: "completed",
  completed_at: "2026-09-30T10:00:00Z",
});

function reviewPageState(items: Review[], page = 1, totalPages = 1) {
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

function wireReviews({
  items = [DRAFT_REVIEW, COMPLETED_REVIEW],
  worklist = [] as Review[],
}: {
  items?: Review[];
  worklist?: Review[];
} = {}) {
  (useListReviews as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (params?: { feedback_for_me?: boolean }) =>
      params?.feedback_for_me ? reviewPageState(worklist) : reviewPageState(items),
  );
}

const createReview = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateReview = vi.fn<(variables: unknown) => Promise<unknown>>();
const completeReview = vi.fn<(variables: unknown) => Promise<unknown>>();
const cancelReview = vi.fn<(variables: unknown) => Promise<unknown>>();
const submitFeedback = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { role: "manager", employee_id: MANAGER_ID };
  wireReviews();
  wireQuery(useListEmployees, requestPageState([MANAGER, WENDY, PEER]));
  wireQuery(useGetCurrentEmployee, itemState(MANAGER_FULL));
  wireQuery(useListReviewFeedback, {
    data: { data: { items: [] }, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  wireMutation(useCreateReview as unknown as MutateFn, createReview);
  wireMutation(useCompleteReview as unknown as MutateFn, completeReview);
  wireMutation(useUpdateReview as unknown as MutateFn, updateReview);
  wireMutation(useCancelReview as unknown as MutateFn, cancelReview);
  wireMutation(useSubmitReviewFeedback as unknown as MutateFn, submitFeedback);
});

describe("ReviewsPanel", () => {
  it("creates a draft review for a direct report and nominates a peer", async () => {
    const user = userEvent.setup();
    createReview.mockResolvedValue({ data: { status: 201 } });
    const { queryClient } = renderWithClient(<ReviewsPanel />);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await screen.findByRole("button", { name: "New review" });
    expect(screen.getAllByText("Wendy Chen").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "New review" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("combobox", { name: "Employee" }));
    await user.click(await screen.findByRole("option", { name: "Wendy Chen" }));
    await user.type(within(dialog).getByLabelText("Period (optional)"), "2026-H1");
    await user.click(within(dialog).getByRole("button", { name: "Priya Nair" }));
    await user.click(within(dialog).getByRole("button", { name: "Create review" }));

    await waitFor(() =>
      expect(createReview).toHaveBeenCalledWith({
        data: {
          employee_id: WENDY_ID,
          period: "2026-H1",
          feedback_provider_ids: [PEER_ID],
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Review created");
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["/api/v1/performance/reviews"],
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("edits a draft with a rating and the evaluative fields", async () => {
    const user = userEvent.setup();
    updateReview.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<ReviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Edit the review for Wendy Chen · 2026-Q3" }),
    );
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("combobox", { name: "Rating" }));
    await user.click(await screen.findByRole("option", { name: "4 / 5" }));
    await user.type(
      within(dialog).getByLabelText("Strengths (optional)"),
      "Carried the migration.",
    );
    await user.type(
      within(dialog).getByLabelText("Improvements (optional)"),
      "Share context earlier.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateReview).toHaveBeenCalledWith({
        id: DRAFT_REVIEW.id,
        data: {
          period: "2026-Q3",
          rating: 4,
          strengths: "Carried the migration.",
          improvements: "Share context earlier.",
          feedback_provider_ids: [PEER_ID],
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Review updated");
  });

  it("keeps the dialog and input when a stale draft refuses the edit", async () => {
    const user = userEvent.setup();
    updateReview.mockRejectedValue(
      apiError(409, "review_not_draft", "Review is no longer a draft"),
    );
    renderWithClient(<ReviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Edit the review for Wendy Chen · 2026-Q3" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox", { name: "Rating" }));
    await user.click(await screen.findByRole("option", { name: "4 / 5" }));
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This review is no longer a draft. Refresh the list and try again.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Rating" })).toHaveTextContent("4 / 5");
  });

  it("refuses completion without a rating with the pinned copy", async () => {
    const user = userEvent.setup();
    completeReview.mockRejectedValue(
      apiError(409, "review_incomplete", "Add a rating before completing the review"),
    );
    renderWithClient(<ReviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Complete the review for Wendy Chen · 2026-Q3" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Complete review" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Add a rating before completing the review.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("completes a review after confirmation", async () => {
    const user = userEvent.setup();
    completeReview.mockResolvedValue({ data: { status: 200 } });
    renderWithClient(<ReviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "Complete the review for Wendy Chen · 2026-Q3" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Complete review" }));

    await waitFor(() => expect(completeReview).toHaveBeenCalledWith({ id: DRAFT_REVIEW.id }));
    expect(toast.success).toHaveBeenCalledWith("Review completed");
  });

  it("shows a subject only completed reviews, with no feedback surface at all", async () => {
    const user = userEvent.setup();
    auth.user = { role: "employee", employee_id: WENDY_ID };
    wireQuery(useGetCurrentEmployee, itemState({ ...WENDY, tenant_id: TENANT_ID }));
    wireReviews({ items: [DRAFT_REVIEW, COMPLETED_REVIEW] });
    renderWithClient(<ReviewsPanel />);

    // The draft in the payload is defensively filtered out of the employee tree.
    expect(await screen.findByText("2026-H1")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "My reviews" })).toBeInTheDocument();
    expect(screen.queryByText("2026-Q3")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Filter by status" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /View the review for/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Owned the migration end to end.")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Bring the team into design reviews earlier."),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("4 / 5")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("heading", { name: "Peer feedback" }),
    ).not.toBeInTheDocument();
    expect(useListReviewFeedback).not.toHaveBeenCalled();
  });

  it("shows the author every peer comment on a completed review", async () => {
    const user = userEvent.setup();
    wireQuery(useListReviewFeedback, {
      data: {
        data: {
          items: [
            {
              id: "01900000-0000-7000-8000-0000000000c1",
              review_id: COMPLETED_REVIEW.id,
              author_id: PEER_ID,
              comment: "Priya says the migration notes were excellent.",
              created_at: "2026-09-29T10:00:00Z",
            },
          ],
        },
        status: 200,
      },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderWithClient(<ReviewsPanel />);

    await user.click(
      await screen.findByRole("button", { name: "View the review for Wendy Chen · 2026-H1" }),
    );
    const dialog = await screen.findByRole("dialog");

    expect(await within(dialog).findByText("Peer feedback")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Priya says the migration notes were excellent."),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Priya Nair")).toBeInTheDocument();
  });

  it("shows the denied state when the API refuses the list", async () => {
    (useListReviews as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (params?: { feedback_for_me?: boolean }) =>
        params?.feedback_for_me
          ? reviewPageState([])
          : {
              data: undefined,
              isPending: false,
              isError: true,
              error: apiError(403, "performance_denied", "Not allowed"),
              refetch: vi.fn(),
            },
    );
    renderWithClient(<ReviewsPanel />);

    expect(
      await screen.findByText("You don't have permission to view these reviews."),
    ).toBeInTheDocument();
  });
});
