/**
 * UserFormDialog feature tests (AC12) — create pins the full `CreateUser`
 * shape; edit PATCHes email/role/link only. Server errors render as visible
 * field errors keyed by the pinned codes (`user_email_taken` → email,
 * `employee_link_taken` → employee) and keep the dialog open. The pinned
 * 10-character password minimum is enforced client-side.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCreateUser, useUpdateUser } from "@workpulse/api-client";
import type { User } from "@workpulse/api-client/index.schemas";
import { toast } from "sonner";

import { useEmployees } from "@/features/employees/hooks/use-employees";
import { UserFormDialog } from "@/features/users/components/UserFormDialog";

import { apiError, listState, wireMutation, wireQuery } from "./helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/features/employees/hooks/use-employees", () => ({
  useEmployees: vi.fn(),
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

const EMPLOYEE_ID = "00000000-0000-4000-8000-000000000021";

const EDIT_USER: User = {
  id: "01900000-0000-7000-8000-0000000000a3",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  email: "worker@workpulse.test",
  role: "employee",
  employee_id: EMPLOYEE_ID,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const mockUseEmployees = vi.mocked(useEmployees);
const createUser = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateUser = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog(user: User | null) {
  const onOpenChange = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <UserFormDialog open onOpenChange={onOpenChange} user={user} />
    </QueryClientProvider>,
  );
  return { onOpenChange };
}

async function selectRole(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(screen.getByRole("combobox", { name: "Role" }));
  await user.click(await screen.findByRole("option", { name: label }));
}

async function selectEmployee(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(screen.getByRole("combobox", { name: "Employee link (optional)" }));
  await user.click(await screen.findByRole("option", { name: label }));
}

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(
    mockUseEmployees,
    listState([
      {
        id: EMPLOYEE_ID,
        employee_code: "EMP-DEV01",
        first_name: "Wendy",
        last_name: "Worker",
        email: "worker@workpulse.test",
        department_id: "01900000-0000-7000-8000-000000000d01",
        position_id: "01900000-0000-7000-8000-000000000201",
        location_id: "01900000-0000-7000-8000-000000000301",
        manager_id: null,
        employment_type: "full_time",
        status: "active",
        hired_at: "2026-01-05",
        terminated_at: null,
      },
    ]),
  );
  wireMutation(useCreateUser, createUser);
  wireMutation(useUpdateUser, updateUser);
});

describe("UserFormDialog (create)", () => {
  it("creates a user with the pinned CreateUser shape and closes", async () => {
    const user = userEvent.setup();
    createUser.mockResolvedValue({ data: { data: EDIT_USER, status: 201 } });
    const { onOpenChange } = renderDialog(null);

    await user.type(screen.getByLabelText("Email"), "alex@workpulse.test");
    await user.type(screen.getByLabelText("Password"), "long-enough-password");
    await selectRole(user, "Manager");
    await selectEmployee(user, "Wendy Worker");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() =>
      expect(createUser).toHaveBeenCalledWith({
        data: {
          email: "alex@workpulse.test",
          password: "long-enough-password",
          role: "manager",
          employee_id: EMPLOYEE_ID,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("User created");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("blocks a password shorter than the pinned minimum", async () => {
    const user = userEvent.setup();
    renderDialog(null);

    await user.type(screen.getByLabelText("Email"), "alex@workpulse.test");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByText("Password must be at least 10 characters")).toBeInTheDocument();
    expect(createUser).not.toHaveBeenCalled();
  });

  it("renders the 409 duplicate email as a visible field error (AC12)", async () => {
    const user = userEvent.setup();
    createUser.mockRejectedValue(apiError(409, "user_email_taken", "User email already taken"));
    const { onOpenChange } = renderDialog(null);

    await user.type(screen.getByLabelText("Email"), "alex@workpulse.test");
    await user.type(screen.getByLabelText("Password"), "long-enough-password");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByText("A user with this email already exists.")).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("User email already taken");
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("renders the 409 linked-employee conflict on the employee field", async () => {
    const user = userEvent.setup();
    createUser.mockRejectedValue(
      apiError(409, "employee_link_taken", "Employee is already linked"),
    );
    renderDialog(null);

    await user.type(screen.getByLabelText("Email"), "alex@workpulse.test");
    await user.type(screen.getByLabelText("Password"), "long-enough-password");
    await selectEmployee(user, "Wendy Worker");
    await user.click(screen.getByRole("button", { name: "Create user" }));

    expect(
      await screen.findByText("This employee is already linked to a user."),
    ).toBeInTheDocument();
  });
});

describe("UserFormDialog (edit)", () => {
  it("patches email/role/link only — no password field, no `active`", async () => {
    const user = userEvent.setup();
    updateUser.mockResolvedValue({ data: { data: EDIT_USER, status: 200 } });
    const { onOpenChange } = renderDialog(EDIT_USER);

    expect(screen.getByRole("heading", { name: "Edit user" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("worker@workpulse.test");

    await selectRole(user, "Manager");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith({
        id: EDIT_USER.id,
        data: {
          email: "worker@workpulse.test",
          role: "manager",
          employee_id: EMPLOYEE_ID,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("User updated");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("unlinks the employee with an explicit null (pinned PATCH semantics)", async () => {
    const user = userEvent.setup();
    updateUser.mockResolvedValue({ data: { data: EDIT_USER, status: 200 } });
    renderDialog(EDIT_USER);

    await selectEmployee(user, "No employee");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith({
        id: EDIT_USER.id,
        data: { email: "worker@workpulse.test", role: "employee", employee_id: null },
      }),
    );
  });
});
