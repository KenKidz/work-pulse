/**
 * DeactivateDialog tests (AC12) — the confirm action carries the user id;
 * the pinned `last_admin` 409 renders as a visible root error and keeps the
 * dialog open.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useDeactivateUser } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";
import { toast } from "sonner";

import { DeactivateDialog } from "@/features/users/components/DeactivateDialog";

import { apiError, wireMutation } from "./helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getListUsersQueryKey: () => ["/api/v1/users"],
  getGetCurrentUserQueryKey: () => ["/api/v1/auth/me"],
  useListUsers: vi.fn(),
  useCreateUser: vi.fn(),
  useUpdateUser: vi.fn(),
  useActivateUser: vi.fn(),
  useDeactivateUser: vi.fn(),
  useResetUserPassword: vi.fn(),
}));

const USER: User = {
  id: "01900000-0000-7000-8000-0000000000a1",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  email: "worker@workpulse.test",
  role: "employee",
  employee_id: null,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const deactivateUser = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog(onOpenChange: (open: boolean) => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <DeactivateDialog open onOpenChange={onOpenChange} user={USER} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useDeactivateUser, deactivateUser);
});

describe("DeactivateDialog", () => {
  it("names the user and deactivates on confirm", async () => {
    const user = userEvent.setup();
    deactivateUser.mockResolvedValue({ data: undefined, status: 200 });
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);

    expect(screen.getByText(/worker@workpulse.test/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Deactivate" }));

    await waitFor(() => expect(deactivateUser).toHaveBeenCalledWith({ id: USER.id }));
    expect(toast.success).toHaveBeenCalledWith("User deactivated");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("renders the pinned last_admin 409 as a visible root error (AC12)", async () => {
    const user = userEvent.setup();
    deactivateUser.mockRejectedValue(
      apiError(409, "last_admin", "The last active admin cannot be deactivated"),
    );
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);

    await user.click(screen.getByRole("button", { name: "Deactivate" }));

    expect(
      await screen.findByText("The last active admin or super admin cannot be deactivated."),
    ).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("closes without a request on cancel", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(deactivateUser).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
