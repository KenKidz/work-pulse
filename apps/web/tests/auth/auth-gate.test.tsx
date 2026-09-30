/**
 * AuthGate tests (AC11) — the generated api-client and the router are
 * mocked; the gate's pending/redirect/resolved behavior and the context it
 * exposes to nav filtering are the real thing.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { logout, useGetCurrentUser } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { AuthGate, useAuth } from "@/features/auth/components/AuthGate";

import { authState, wireQuery } from "./helpers";

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
  logout: vi.fn(),
  useGetCurrentUser: vi.fn(),
  useLogin: vi.fn(),
  useLogout: vi.fn(),
}));

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

function GateProbe() {
  const { user } = useAuth();
  return <p>{`signed in as ${user.email}`}</p>;
}

function renderGate() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <GateProbe />
      </AuthGate>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(logout).mockResolvedValue({
    data: undefined,
    status: 204,
    headers: new Headers(),
  });
});

describe("AuthGate", () => {
  it("shows a checking state while the session resolves", () => {
    wireQuery(useGetCurrentUser, authState(null, { data: undefined, isPending: true }));
    renderGate();

    expect(screen.getByText("Checking your session…")).toBeInTheDocument();
  });

  it("signs the stale session out and redirects to the login page when unusable", async () => {
    // The idempotent logout clears the stale `wp_session` cookie first —
    // without it the cookie-presence proxy would loop /login ↔ /dashboard.
    wireQuery(useGetCurrentUser, authState(null));
    renderGate();

    await waitFor(() => expect(logout).toHaveBeenCalled());
    await waitFor(() => expect(routerMocks.replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText(/signed in as/)).not.toBeInTheDocument();
  });

  it("renders children with the resolved user in context", async () => {
    wireQuery(useGetCurrentUser, authState(SEEDED_USER));
    renderGate();

    expect(await screen.findByText("signed in as admin@workpulse.test")).toBeInTheDocument();
    expect(logout).not.toHaveBeenCalled();
    expect(routerMocks.replace).not.toHaveBeenCalled();
  });
});
