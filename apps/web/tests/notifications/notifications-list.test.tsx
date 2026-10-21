/**
 * NotificationsList — the `/notifications` inbox: stored message of record,
 * unread/read distinction, per-item and bulk read actions, pagination, and the
 * empty/error states. The generated client is mocked; hooks and component are
 * the real thing.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useGetNotificationUnreadCount,
  useListNotifications,
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { NotificationsList } from "@/features/notifications/components/NotificationsList";

import {
  apiError,
  notificationPageState,
  renderWithClient,
  unreadCountState,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "./helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getListNotificationsQueryKey: () => ["/api/v1/notifications"],
  getGetNotificationUnreadCountQueryKey: () => ["/api/v1/notifications/unread-count"],
  useListNotifications: vi.fn(),
  useGetNotificationUnreadCount: vi.fn(),
  useMarkNotificationRead: vi.fn(),
  useMarkAllNotificationsRead: vi.fn(),
}));

const UNREAD_ONE = {
  id: "01900000-0000-7000-8000-00000000n001",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  kind: "leave_requested" as const,
  subject: "Leave request from Wendy Worker",
  body: "Wendy Worker requested unpaid leave.",
  created_at: "2026-10-21T03:00:00Z",
  read_at: null,
};

const UNREAD_TWO = {
  ...UNREAD_ONE,
  id: "01900000-0000-7000-8000-00000000n002",
  kind: "attendance_late" as const,
  subject: "Late check-in recorded",
  body: "A late check-in was recorded for your team.",
  created_at: "2026-10-20T03:00:00Z",
};

const READ = {
  ...UNREAD_ONE,
  id: "01900000-0000-7000-8000-00000000n003",
  kind: "leave_withdrawn" as const,
  subject: "Leave request withdrawn",
  body: "Wendy Worker withdrew a leave request.",
  created_at: "2026-10-19T03:00:00Z",
  read_at: "2026-10-19T04:00:00Z",
};

const markRead = vi.fn<(variables: unknown) => Promise<unknown>>();
const markAll = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useGetNotificationUnreadCount, unreadCountState(2));
  wireQuery(useListNotifications, notificationPageState([UNREAD_ONE, UNREAD_TWO, READ]));
  wireMutation(useMarkNotificationRead as unknown as MutateFn, markRead);
  wireMutation(useMarkAllNotificationsRead as unknown as MutateFn, markAll);
  markRead.mockResolvedValue({ data: undefined, status: 204 });
  markAll.mockResolvedValue({ data: undefined, status: 204 });
});

describe("NotificationsList", () => {
  it("renders the stored message of record with unread and read distinction", () => {
    renderWithClient(<NotificationsList />);

    expect(screen.getByText("Leave request from Wendy Worker")).toBeInTheDocument();
    expect(screen.getByText("Wendy Worker requested unpaid leave.")).toBeInTheDocument();
    expect(screen.getByText("Leave request")).toBeInTheDocument();
    expect(screen.getByText("Late check-in")).toBeInTheDocument();
    expect(screen.getByText("Leave withdrawn")).toBeInTheDocument();

    const rows = screen.getAllByTestId("notification-row");
    expect(rows.map((row) => row.getAttribute("data-read-state"))).toEqual([
      "unread",
      "unread",
      "read",
    ]);
    expect(screen.getAllByText("Unread")).toHaveLength(2);
    expect(screen.getByText("Read")).toBeInTheDocument();
    expect(screen.getByText("2 unread")).toBeInTheDocument();
  });

  it("marks one notification read and refreshes the list and badge", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderWithClient(<NotificationsList />);
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await user.click(
      screen.getByRole("button", {
        name: "Mark read: Leave request from Wendy Worker",
      }),
    );

    await waitFor(() => expect(markRead).toHaveBeenCalledWith({ id: UNREAD_ONE.id }));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["/api/v1/notifications"],
      }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["/api/v1/notifications/unread-count"],
    });
    expect(toast.success).toHaveBeenCalledWith("Notification marked read");
  });

  it("marks all read and reports the outcome", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderWithClient(<NotificationsList />);
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await user.click(screen.getByRole("button", { name: "Mark all read" }));

    await waitFor(() => expect(markAll).toHaveBeenCalled());
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["/api/v1/notifications/unread-count"],
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("All notifications marked read");
  });

  it("disables mark-all when nothing is unread", () => {
    wireQuery(useGetNotificationUnreadCount, unreadCountState(0));
    renderWithClient(<NotificationsList />);

    expect(screen.getByRole("button", { name: "Mark all read" })).toBeDisabled();
    expect(screen.getByText("You’re all caught up")).toBeInTheDocument();
  });

  it("renders the empty state instead of an empty list", () => {
    wireQuery(useGetNotificationUnreadCount, unreadCountState(0));
    wireQuery(useListNotifications, notificationPageState([]));
    renderWithClient(<NotificationsList />);

    expect(
      screen.getByText(
        "No notifications yet. Leave and attendance updates addressed to you will appear here.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryAllByTestId("notification-row")).toHaveLength(0);
  });

  it("renders a retryable error state without breaking the section", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    wireQuery(
      useListNotifications,
      notificationPageState([], {
        isError: true,
        error: apiError(500, "internal_error", "boom"),
        refetch,
      }),
    );
    renderWithClient(<NotificationsList />);

    expect(screen.getByText("Could not load your notifications.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("pages forward through the inbox", async () => {
    const user = userEvent.setup();
    wireQuery(
      useListNotifications,
      notificationPageState([UNREAD_ONE], {
        data: {
          data: {
            items: [UNREAD_ONE],
            page: { page: 1, page_size: 20, total_items: 21, total_pages: 2 },
          },
          status: 200,
        },
      }),
    );
    renderWithClient(<NotificationsList />);

    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(useListNotifications).toHaveBeenLastCalledWith(
        { page: 2, page_size: 20 },
        expect.anything(),
      ),
    );
  });
});
