/**
 * LoginForm feature tests (AC11/AC13) — the generated api-client layer and
 * the Next.js router are mocked; the form, the error mapping and the
 * redirects are the real thing. Covers the happy path, the visible
 * `invalid_credentials` error state (stays on the page), client validation
 * and the already-authenticated redirect.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useGetCurrentUser, useLogin } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";

import { LoginForm } from "@/features/auth/components/LoginForm";

import { apiError, authState, wireMutation, wireQuery } from "./helpers";

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

const loginMutation = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderForm() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <LoginForm />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useLogin, loginMutation);
  // Signed out by default — the expected state on `/login`.
  wireQuery(useGetCurrentUser, authState(null));
});

describe("LoginForm", () => {
  it("renders the email and password fields with a Sign in submit", () => {
    renderForm();

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("logs in and routes to the dashboard on success", async () => {
    const user = userEvent.setup();
    loginMutation.mockResolvedValue({ data: { data: SEEDED_USER, status: 200 } });
    renderForm();

    await user.type(screen.getByLabelText("Email"), "admin@workpulse.test");
    await user.type(screen.getByLabelText("Password"), "workpulse-dev");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() =>
      expect(loginMutation).toHaveBeenCalledWith({
        data: { email: "admin@workpulse.test", password: "workpulse-dev" },
      }),
    );
    await waitFor(() => expect(routerMocks.push).toHaveBeenCalledWith("/dashboard"));
  });

  it("shows the invalid_credentials error state and stays on the page (AC13)", async () => {
    const user = userEvent.setup();
    loginMutation.mockRejectedValue(
      apiError(401, "invalid_credentials", "Unknown email or wrong password"),
    );
    renderForm();

    await user.type(screen.getByLabelText("Email"), "admin@workpulse.test");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(routerMocks.push).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("blocks the submit on client validation", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Email is required")).toBeInTheDocument();
    expect(loginMutation).not.toHaveBeenCalled();
  });

  it("routes an already-authenticated visitor to the dashboard", async () => {
    wireQuery(useGetCurrentUser, authState(SEEDED_USER));
    renderForm();

    await waitFor(() => expect(routerMocks.replace).toHaveBeenCalledWith("/dashboard"));
  });
});
