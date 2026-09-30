/**
 * ResetPasswordDialog tests (AC12) — the admin reset pins the same
 * 10-character minimum as creation; success closes the dialog, the client
 * validation keeps it open with a visible message.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useResetUserPassword } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";
import { toast } from "sonner";

import { ResetPasswordDialog } from "@/features/users/components/ResetPasswordDialog";

import { wireMutation } from "./helpers";

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

const resetPassword = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog(onOpenChange: (open: boolean) => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ResetPasswordDialog open onOpenChange={onOpenChange} user={USER} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  resetPassword.mockResolvedValue({ data: undefined, status: 204 });
  wireMutation(useResetUserPassword, resetPassword);
});

describe("ResetPasswordDialog", () => {
  it("names the user and resets with the new password", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);

    expect(screen.getByText(/worker@workpulse.test/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("New password"), "a-fresh-password");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() =>
      expect(resetPassword).toHaveBeenCalledWith({
        id: USER.id,
        data: { new_password: "a-fresh-password" },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Password reset");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("blocks a password shorter than the pinned minimum", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    renderDialog(onOpenChange);

    await user.type(screen.getByLabelText("New password"), "short");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByText("Password must be at least 10 characters")).toBeInTheDocument();
    expect(resetPassword).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
