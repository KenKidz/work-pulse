/**
 * Feedback worklist and immutable submission — a nominated peer sees only the
 * review shell, files exactly one comment, and a second attempt keeps the copy
 * and shows the pinned refusal. The review author's feedback panel is the only
 * surface that reads every comment. The generated api-client is mocked
 * wholesale.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useGetCurrentEmployee,
  useListEmployees,
  useListReviewFeedback,
  useListReviews,
  useSubmitReviewFeedback,
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
    role: "employee" as string,
    employee_id: "01900000-0000-7000-8000-0000000000a3" as string | null,
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

const DRAFT_REVIEW: Review = {
  id: "01900000-0000-7000-8000-0000000000b1",
  employee_id: WENDY_ID,
  period: "2026-Q3",
  rating: null,
  strengths: null,
  improvements: null,
  status: "draft",
  feedback_provider_ids: [PEER_ID],
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
  completed_at: null,
};

const COMPLETED_REVIEW: Review = {
  ...DRAFT_REVIEW,
  id: "01900000-0000-7000-8000-0000000000b2",
  period: "2026-H1",
  rating: 4,
  strengths: "Owned the migration end to end.",
  improvements: "Bring the team into design reviews earlier.",
  status: "completed",
  completed_at: "2026-09-30T10:00:00Z",
};

function reviewPageState(items: Review[]) {
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
  };
}

function wireReviews({
  items = [] as Review[],
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

function emptyFeedbackState() {
  return {
    data: { data: { items: [] }, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

const submitFeedback = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { role: "employee", employee_id: PEER_ID };
  wireReviews({ items: [], worklist: [DRAFT_REVIEW] });
  wireQuery(useListEmployees, requestPageState([MANAGER, WENDY, PEER]));
  wireQuery(useGetCurrentEmployee, itemState(MANAGER_FULL));
  wireQuery(useListReviewFeedback, emptyFeedbackState());
  wireMutation(useSubmitReviewFeedback as unknown as MutateFn, submitFeedback);
});

describe("feedback worklist", () => {
  it("lets a nominated peer file exactly one comment", async () => {
    const user = userEvent.setup();
    submitFeedback.mockResolvedValue({ data: { status: 201 } });
    renderWithClient(<ReviewsPanel />);

    expect(await screen.findByText("Feedback requested")).toBeInTheDocument();
    // The provider sees the worklist shell: the subject and state only, never
    // the draft's rating or evaluative text.
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.queryByText("4 / 5")).not.toBeInTheDocument();
    expect(screen.queryByText("Owned the migration end to end.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Give feedback/ }));
    const dialog = await screen.findByRole("dialog");

    await user.type(
      within(dialog).getByLabelText("Comment"),
      "Wendy kept the release on track during the migration.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Submit feedback" }));

    await waitFor(() =>
      expect(submitFeedback).toHaveBeenCalledWith({
        id: DRAFT_REVIEW.id,
        data: { comment: "Wendy kept the release on track during the migration." },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Feedback submitted");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("refuses a second submission and keeps the typed comment", async () => {
    const user = userEvent.setup();
    submitFeedback.mockRejectedValue(
      apiError(409, "feedback_already_submitted", "You already submitted feedback"),
    );
    renderWithClient(<ReviewsPanel />);

    await user.click(await screen.findByRole("button", { name: /Give feedback/ }));
    const dialog = await screen.findByRole("dialog");
    const comment = within(dialog).getByLabelText("Comment");
    await user.type(comment, "A second attempt.");
    await user.click(within(dialog).getByRole("button", { name: "Submit feedback" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "You already submitted feedback for this review.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(comment).toHaveValue("A second attempt.");
  });

  it("explains a stale nomination refusal without leaking the review", async () => {
    const user = userEvent.setup();
    submitFeedback.mockRejectedValue(apiError(403, "performance_denied", "Not allowed"));
    renderWithClient(<ReviewsPanel />);

    await user.click(await screen.findByRole("button", { name: /Give feedback/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Comment"), "Still allowed?");
    await user.click(within(dialog).getByRole("button", { name: "Submit feedback" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "You're no longer nominated for this review, so it can't accept your feedback.",
    );
  });

  it("shows the author every peer comment on the review detail", async () => {
    const user = userEvent.setup();
    auth.user = { role: "manager", employee_id: MANAGER_ID };
    wireReviews({ items: [COMPLETED_REVIEW], worklist: [] });
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
            {
              id: "01900000-0000-7000-8000-0000000000c2",
              review_id: COMPLETED_REVIEW.id,
              author_id: WENDY_ID,
              comment: "Wendy asked for earlier context in design reviews.",
              created_at: "2026-09-29T11:00:00Z",
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
    expect(
      within(dialog).getByText("Wendy asked for earlier context in design reviews."),
    ).toBeInTheDocument();
  });
});
