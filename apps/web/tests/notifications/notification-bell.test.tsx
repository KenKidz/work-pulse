/**
 * NotificationBell — the header badge and recent-notifications dropdown.
 *
 * The generated client is mocked; the hooks, cache invalidation and component
 * are the real thing. `sonner` is mocked so toasts are assertable and do not
 * render.
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

import { NotificationBell } from "@/features/notifications/components/NotificationBell";

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

const UNREAD = {
  id: "01900000-0000-7000-8000-00000000n001",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  kind: "leave_approved" as const,
  subject: "Your leave request was approved",
  body: "Your unpaid leave for 2026-10-21 was approved.",
  created_at: "2026-10-21T03:00:00Z",
  read_at: null,
};

const READ = {
  ...UNREAD,
  id: "01900000-0000-7000-8000-00000000n002",
  kind: "attendance_late" as const,
  subject: "Late check-in recorded",
  body: "A late check-in was recorded for your team.",
  created_at: "2026-10-20T03:00:00Z",
  read_at: "2026-10-20T04:00:00Z",
};

const markAll = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderBell() {
  return renderWithClient(<NotificationBell />);
}

function trigger(): HTMLElement {
  return screen.getByTestId("notification-bell");
}

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useGetNotificationUnreadCount, unreadCountState(3));
  wireQuery(useListNotifications, notificationPageState([UNREAD, READ]));
  wireMutation(useMarkNotificationRead as unknown as MutateFn, vi.fn());
  wireMutation(useMarkAllNotificationsRead as unknown as MutateFn, markAll);
  markAll.mockResolvedValue({ data: undefined, status: 204 });
});

describe("NotificationBell", () => {
  it("shows the unread badge and a named trigger", () => {
    renderBell();

    expect(screen.getByTestId("notification-unread-badge")).toHaveTextContent("3");
    expect(trigger()).toHaveAccessibleName("Notifications, 3 unread");
  });

  it("opens the recent list with unread/read markers and a link to the inbox", async () => {
    const user = userEvent.setup();
    renderBell();

    await user.click(trigger());

    expect(screen.getAllByTestId("notification-preview")).toHaveLength(2);
    expect(screen.getByText("Your leave request was approved")).toBeInTheDocument();
    expect(screen.getByText("Leave approved")).toBeInTheDocument();
    expect(screen.getByText("Late check-in")).toBeInTheDocument();
    expect(screen.getByText("Unread")).toBeInTheDocument();
    expect(
      screen
        .getAllByTestId("notification-preview")
        .map((row) => row.getAttribute("data-read-state")),
    ).toEqual(["unread", "read"]);

    const link = screen.getByRole("menuitem", { name: "View all notifications" });
    expect(link).toHaveAttribute("href", "/notifications");
  });

  it("hides the badge when nothing is unread", () => {
    wireQuery(useGetNotificationUnreadCount, unreadCountState(0));
    renderBell();

    expect(screen.queryByTestId("notification-unread-badge")).not.toBeInTheDocument();
    expect(trigger()).toHaveAccessibleName("Notifications");
  });

  it("degrades quietly when the count query fails", async () => {
    wireQuery(
      useGetNotificationUnreadCount,
      unreadCountState(0, { isError: true, error: apiError(500, "internal_error", "boom") }),
    );
    const user = userEvent.setup();
    renderBell();

    expect(screen.queryByTestId("notification-unread-badge")).not.toBeInTheDocument();
    // The menu still opens, and the failure is not surfaced as a toast.
    await user.click(trigger());
    expect(screen.getByRole("menuitem", { name: "View all notifications" })).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("shows the empty message inside the dropdown", async () => {
    wireQuery(useGetNotificationUnreadCount, unreadCountState(0));
    wireQuery(useListNotifications, notificationPageState([]));
    const user = userEvent.setup();
    renderBell();

    await user.click(trigger());

    expect(screen.getByText("No notifications yet.")).toBeInTheDocument();
  });

  it("marks all read from the dropdown and refreshes the badge and list", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderBell();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    await user.click(trigger());
    await user.click(screen.getByRole("menuitem", { name: "Mark all read" }));

    await waitFor(() => expect(markAll).toHaveBeenCalled());
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["/api/v1/notifications"],
      }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["/api/v1/notifications/unread-count"],
    });
    expect(toast.success).toHaveBeenCalledWith("All notifications marked read");
  });
});
