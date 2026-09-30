/**
 * UserMenu tests — the header account menu: an initials-avatar trigger whose
 * dropdown holds the identity block (email + role badge) and the sign-out
 * action. The auth context is mocked to a signed-in user; the logout hook
 * (cache clear + routing) is the real thing over a mocked generated client.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLogout } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";
import { toast } from "sonner";

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

vi.mock("sonner", () => ({
  toast: { error: vi.fn() },
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
const mockUseLogout = vi.mocked(useLogout);
const toastError = vi.mocked(toast.error);

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

function getTrigger(): HTMLElement {
  return screen.getByRole("button", {
    name: `Account menu for ${SEEDED_USER.email}`,
  });
}

async function openMenu(): Promise<void> {
  await userEvent.setup().click(getTrigger());
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: SEEDED_USER });
  logoutMutation.mockResolvedValue({ data: undefined, status: 204 });
  wireMutation(useLogout, logoutMutation);
});

describe("UserMenu", () => {
  it("keeps the email out of the bar and exposes a named trigger", () => {
    renderMenu();

    // The identity lives inside the dropdown — the bar shows the avatar only.
    expect(screen.queryByText(SEEDED_USER.email)).not.toBeInTheDocument();
    expect(getTrigger()).toBeInTheDocument();
  });

  it("renders initials derived from the email in the trigger", () => {
    renderMenu();

    // `admin` → `A`.
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("opening the menu reveals the identity block and the sign-out action", async () => {
    renderMenu();

    await openMenu();

    expect(screen.getByText(SEEDED_USER.email)).toBeInTheDocument();
    // roleLabel renders the wire value, never the raw `admin` string.
    expect(screen.getByText("Admin")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: "Sign out" }),
    ).toBeInTheDocument();
  });

  it("signing out calls logout, clears the cache and routes to the login page", async () => {
    const user = userEvent.setup();
    const { clearSpy } = renderMenu();

    await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    await waitFor(() => expect(logoutMutation).toHaveBeenCalled());
    await waitFor(() => expect(clearSpy).toHaveBeenCalled());
    await waitFor(() => expect(routerMocks.push).toHaveBeenCalledWith("/login"));
    expect(routerMocks.refresh).toHaveBeenCalled();
  });

  it("reports a failed logout instead of routing to the login page", async () => {
    const user = userEvent.setup();
    logoutMutation.mockRejectedValueOnce(new Error("network down"));
    renderMenu();

    await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    // The pinned message, never the raw error; the user stays signed in.
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        "Something went wrong. Please try again.",
      ),
    );
    expect(routerMocks.push).not.toHaveBeenCalledWith("/login");
  });

  it("shows a disabled signing-out row while logout is pending", async () => {
    mockUseLogout.mockImplementation(
      (() => ({
        mutateAsync: () => new Promise<never>(() => {}),
        isPending: true,
      })) as never,
    );
    renderMenu();

    await openMenu();

    expect(
      screen.getByRole("menuitem", { name: "Signing out…" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("renders the role badge for a non-admin account", async () => {
    mockUseAuth.mockReturnValue({
      user: { ...SEEDED_USER, role: "hr" as User["role"] },
    });
    renderMenu();

    await openMenu();

    expect(screen.getByText("HR")).toBeInTheDocument();
  });
});
