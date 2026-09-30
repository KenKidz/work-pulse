/**
 * UserMenu tests — the header identity + logout action. The auth context is
 * mocked to a signed-in user; the logout hook (cache clear + routing) is the
 * real thing over a mocked generated client.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLogout } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { useAuth } from "@/features/auth/components/AuthGate";
import { UserMenu } from "@/features/auth/components/UserMenu";

import { wireMutation } from "./helpers";

const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMocks,
}));

vi.mock("@workpulse/api-client", () => ({
  getGetCurrentUserQueryKey: () => ["/api/v1/auth/me"],
  useGetCurrentUser: vi.fn(),
  useLogin: vi.fn(),
  useLogout: vi.fn(),
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: vi.fn(),
}));

const mockUseAuth = vi.mocked(useAuth);

const SEEDED_USER: User = {
  id: "01900000-0000-7000-8000-000000000011",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  email: "admin@workpulse.test",
  role: "admin",
  employee_id: null,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const logoutMutation = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderMenu() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const clearSpy = vi.spyOn(queryClient, "clear");
  render(
    <QueryClientProvider client={queryClient}>
      <UserMenu />
    </QueryClientProvider>,
  );
  return { clearSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: SEEDED_USER });
  logoutMutation.mockResolvedValue({ data: undefined, status: 204 });
  wireMutation(useLogout, logoutMutation);
});

describe("UserMenu", () => {
  it("shows the signed-in account email and a Sign out action", () => {
    renderMenu();

    expect(screen.getByText("admin@workpulse.test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("signing out calls logout, clears the cache and routes to the login page", async () => {
    const user = userEvent.setup();
    const { clearSpy } = renderMenu();

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(logoutMutation).toHaveBeenCalled());
    await waitFor(() => expect(clearSpy).toHaveBeenCalled());
    await waitFor(() => expect(routerMocks.push).toHaveBeenCalledWith("/login"));
    expect(routerMocks.refresh).toHaveBeenCalled();
  });
});
