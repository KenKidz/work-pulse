/**
 * UsersTable feature tests (AC12) — the generated api-client layer, the auth
 * context and the employee picker are mocked; hooks, error mapping, table
 * states and the dialogs are the real thing. Covers loading/empty/error,
 * filters (with the drop-not-null regression), pagination, the admin-only
 * guard redirect, the deactivate confirm (incl. the pinned `last_admin`
 * error) and the activate row action.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useActivateUser,
  useCreateUser,
  useDeactivateUser,
  useListUsers,
  useResetUserPassword,
  useUpdateUser,
} from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";
import { toast } from "sonner";

import { useAuth } from "@/features/auth/components/AuthGate";
import { useEmployees } from "@/features/employees/hooks/use-employees";
import { USERS_PAGE_SIZE, UsersTable } from "@/features/users/components/UsersTable";

import { apiError, listState, wireMutation, wireQuery } from "./helpers";

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
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: vi.fn(),
}));

vi.mock("@/features/employees/hooks/use-employees", () => ({
  useEmployees: vi.fn(),
}));

vi.mock("@workpulse/api-client", () => ({
  // Query keys used by the feature invalidation helpers.
  getListUsersQueryKey: () => ["/api/v1/users"],
  getGetCurrentUserQueryKey: () => ["/api/v1/auth/me"],
  // Hooks.
  useListUsers: vi.fn(),
  useCreateUser: vi.fn(),
  useUpdateUser: vi.fn(),
  useActivateUser: vi.fn(),
  useDeactivateUser: vi.fn(),
  useResetUserPassword: vi.fn(),
}));

const TENANT = "01900000-0000-7000-8000-000000000001";
const WORKER_EMPLOYEE_ID = "00000000-0000-4000-8000-000000000021";

const ADMIN_USER: User = {
  id: "01900000-0000-7000-8000-0000000000a1",
  tenant_id: TENANT,
  email: "admin@workpulse.test",
  role: "admin",
  employee_id: null,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const HR_USER: User = {
  ...ADMIN_USER,
  id: "01900000-0000-7000-8000-0000000000a2",
  email: "hr@workpulse.test",
  role: "hr",
};

const WORKER_USER: User = {
  ...ADMIN_USER,
  id: "01900000-0000-7000-8000-0000000000a3",
  email: "worker@workpulse.test",
  role: "employee",
  employee_id: WORKER_EMPLOYEE_ID,
};

const INACTIVE_USER: User = {
  ...ADMIN_USER,
  id: "01900000-0000-7000-8000-0000000000a4",
  email: "gone@workpulse.test",
  active: false,
};

const mockUseAuth = vi.mocked(useAuth);
const mockUseEmployees = vi.mocked(useEmployees);

const activateUser = vi.fn<(variables: unknown) => Promise<unknown>>();
const deactivateUser = vi.fn<(variables: unknown) => Promise<unknown>>();

/** All list calls carrying the table's page size (shared mocked hook). */
function tableCalls() {
  return vi
    .mocked(useListUsers)
    .mock.calls.map(([params]) => params)
    .filter((params) => params?.page_size === USERS_PAGE_SIZE);
}

function renderTable() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <UsersTable />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: ADMIN_USER });
  wireQuery(mockUseEmployees, listState([]));
  wireMutation(useActivateUser, activateUser);
  wireMutation(useDeactivateUser, deactivateUser);
  wireMutation(useCreateUser, vi.fn());
  wireMutation(useUpdateUser, vi.fn());
  wireMutation(useResetUserPassword, vi.fn());
  wireQuery(useListUsers, listState([ADMIN_USER, WORKER_USER]));
});

describe("UsersTable", () => {
  it("renders fetched users with role labels, statuses and the employee link", async () => {
    renderTable();

    expect(await screen.findByText("admin@workpulse.test")).toBeInTheDocument();

    const adminRow = screen
      .getAllByRole("row")
      .find((row) => row.textContent?.includes("admin@workpulse.test"));
    const workerRow = screen
      .getAllByRole("row")
      .find((row) => row.textContent?.includes("worker@workpulse.test"));
    expect(adminRow).toBeDefined();
    expect(workerRow).toBeDefined();
    expect(within(adminRow!).getByText("Admin")).toBeInTheDocument();
    expect(within(adminRow!).getByText("—")).toBeInTheDocument();
    expect(within(workerRow!).getByText("Employee")).toBeInTheDocument();
    expect(
      within(workerRow!).getByText(`Employee …${WORKER_EMPLOYEE_ID.slice(-4)}`),
    ).toBeInTheDocument();
    expect(within(workerRow!).getByText("Active")).toBeInTheDocument();
  });

  it("shows a dash for users without an employee link", async () => {
    renderTable();

    expect(await screen.findByText("—")).toBeInTheDocument();
  });

  it("shows a loading state", () => {
    wireQuery(useListUsers, listState([], { data: undefined, isPending: true }));

    renderTable();

    expect(screen.getByText("Loading users…")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    wireQuery(useListUsers, listState([]));

    renderTable();

    expect(await screen.findByText(/No users match/i)).toBeInTheDocument();
  });

  it("shows an error state with a retry action", async () => {
    wireQuery(
      useListUsers,
      listState([], { data: undefined, isError: true, error: new Error("boom") }),
    );

    renderTable();

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load users.");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("applies the q, role and status filters to the list query", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.type(await screen.findByRole("textbox", { name: "Search users" }), "adm");
    await waitFor(() =>
      expect(useListUsers).toHaveBeenCalledWith(expect.objectContaining({ q: "adm" })),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by role" }));
    await user.click(await screen.findByRole("option", { name: "Admin" }));
    await waitFor(() =>
      expect(useListUsers).toHaveBeenCalledWith(
        expect.objectContaining({ role: "admin", page: 1 }),
      ),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Inactive" }));
    await waitFor(() =>
      expect(useListUsers).toHaveBeenCalledWith(
        expect.objectContaining({ active: false, page: 1 }),
      ),
    );
  });

  it("debounces the search input: one request per pause, none per keystroke", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.type(await screen.findByRole("textbox", { name: "Search users" }), "adm");

    await waitFor(() =>
      expect(useListUsers).toHaveBeenCalledWith(expect.objectContaining({ q: "adm" })),
    );

    // The committed query only ever saw the final value — no per-keystroke
    // requests for "a" or "ad" on the way there.
    const committed = tableCalls()
      .map((params) => params?.q)
      .filter((q) => q !== undefined);
    expect(committed).toEqual(["adm"]);
  });

  it("clearing a filter drops the parameter instead of sending null (422 regression)", async () => {
    // The generated client serializes null as the literal "null" query value;
    // the API 422s parsing it, so "All statuses" must omit `active`.
    const user = userEvent.setup();
    renderTable();

    await user.click(await screen.findByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Inactive" }));
    await waitFor(() =>
      expect(useListUsers).toHaveBeenCalledWith(expect.objectContaining({ active: false })),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "All statuses" }));

    await waitFor(() => {
      const tableCalls = vi
        .mocked(useListUsers)
        .mock.calls.map(([params]) => params)
        .filter((params) => params?.page_size === USERS_PAGE_SIZE);
      const lastParams = tableCalls[tableCalls.length - 1];
      expect(lastParams).toEqual(expect.objectContaining({ active: undefined }));
      expect(Object.values(lastParams ?? {})).not.toContain(null);
    });
  });

  it("paginates", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 3 }, (_, index) => ({
      ...ADMIN_USER,
      id: `01900000-0000-7000-8000-00000000a0${index}`,
      email: `user${index}@workpulse.test`,
    }));
    wireQuery(
      useListUsers,
      listState(many, {
        data: {
          data: {
            items: many,
            page: { page: 1, page_size: 3, total_items: 9, total_pages: 3 },
          },
          status: 200,
        },
      }),
    );

    renderTable();
    await user.click(await screen.findByRole("button", { name: "Next page" }));

    await waitFor(() =>
      expect(useListUsers).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })),
    );
  });

  it("redirects a non-admin visit to the dashboard and renders nothing (AC12)", async () => {
    mockUseAuth.mockReturnValue({ user: HR_USER });
    renderTable();

    await waitFor(() => expect(routerMocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("admin@workpulse.test")).not.toBeInTheDocument();
  });

  it("deactivates a user after confirmation and invalidates the list", async () => {
    const user = userEvent.setup();
    deactivateUser.mockResolvedValue({ data: undefined, status: 200 });
    const { invalidateSpy } = renderTable();

    await user.click(
      await screen.findByRole("button", { name: "Deactivate admin@workpulse.test" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    await waitFor(() => expect(deactivateUser).toHaveBeenCalledWith({ id: ADMIN_USER.id }));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["/api/v1/users"] }),
    );
    expect(toast.success).toHaveBeenCalledWith("User deactivated");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("renders the pinned last_admin 409 as a visible error and keeps the dialog open", async () => {
    const user = userEvent.setup();
    deactivateUser.mockRejectedValue(
      apiError(409, "last_admin", "The last active admin cannot be deactivated"),
    );
    renderTable();

    await user.click(
      await screen.findByRole("button", { name: "Deactivate admin@workpulse.test" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    expect(
      await screen.findByText("The last active admin or super admin cannot be deactivated."),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("activates an inactive user from the row action", async () => {
    const user = userEvent.setup();
    activateUser.mockResolvedValue({ data: { data: INACTIVE_USER, status: 200 } });
    wireQuery(useListUsers, listState([INACTIVE_USER]));

    renderTable();
    await user.click(await screen.findByRole("button", { name: "Activate gone@workpulse.test" }));

    await waitFor(() => expect(activateUser).toHaveBeenCalledWith({ id: INACTIVE_USER.id }));
    expect(toast.success).toHaveBeenCalledWith("User activated");
  });
});
