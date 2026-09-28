/**
 * EmployeeFormDialog tests (AC12) — create happy path with the pinned
 * `CreateEmployee` shape, the 409 duplicate-email visible error state, and
 * edit mode patching profile fields only (pinned PATCH scope).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useListEmployees,
  useCreateEmployee,
  useUpdateEmployee,
  useListDepartments,
  useListPositions,
  useListLocations,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { EmployeeFormDialog } from "@/features/employees/components/EmployeeFormDialog";

import { apiError, listState, wireMutation, wireQuery } from "./helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  // Query keys used by the feature invalidation helpers.
  getListEmployeesQueryKey: () => ["/api/v1/employees"],
  getGetEmployeeQueryKey: (id: string) => [`/api/v1/employees/${id}`],
  getListEmploymentsQueryKey: (id: string) => [`/api/v1/employees/${id}/employments`],
  getListDepartmentsQueryKey: () => ["/api/v1/departments"],
  getListPositionsQueryKey: () => ["/api/v1/positions"],
  getListLocationsQueryKey: () => ["/api/v1/locations"],
  // Hooks.
  useListEmployees: vi.fn(),
  useGetEmployee: vi.fn(),
  useCreateEmployee: vi.fn(),
  useUpdateEmployee: vi.fn(),
  useTransferEmployee: vi.fn(),
  useTerminateEmployee: vi.fn(),
  useListDepartments: vi.fn(),
  useListPositions: vi.fn(),
  useListLocations: vi.fn(),
}));

const DEPARTMENT = {
  id: "01900000-0000-7000-8000-000000000d01",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  name: "Engineering",
  parent_department_id: null,
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const POSITION = {
  id: "01900000-0000-7000-8000-000000000201",
  tenant_id: DEPARTMENT.tenant_id,
  title: "Backend Engineer",
  description: null,
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const LOCATION = {
  id: "01900000-0000-7000-8000-000000000301",
  tenant_id: DEPARTMENT.tenant_id,
  name: "Ho Chi Minh Office",
  address: null,
  timezone: "Asia/Ho_Chi_Minh",
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const MANAGER = {
  id: "01900000-0000-7000-8000-00000000a002",
  employee_code: "EMP-00002",
  first_name: "Morgan",
  last_name: "Reed",
  email: "morgan@example.com",
  department_id: DEPARTMENT.id,
  position_id: "01900000-0000-7000-8000-000000000201",
  location_id: "01900000-0000-7000-8000-000000000301",
  manager_id: null,
  employment_type: "full_time" as const,
  status: "active" as const,
  hired_at: "2025-06-01",
  terminated_at: null,
};

const createEmployee = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateEmployee = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog(props?: { edit?: boolean }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <EmployeeFormDialog
        open
        onOpenChange={() => undefined}
        employee={
          props?.edit
            ? {
                id: MANAGER.id,
                first_name: "Morgan",
                last_name: "Reed",
                email: "morgan@example.com",
                phone: null,
              }
            : null
        }
      />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useCreateEmployee, createEmployee);
  wireMutation(useUpdateEmployee, updateEmployee);
  // The dialog's manager picker is an employees query (active only).
  wireQuery(useListEmployees, listState([MANAGER]));
  wireQuery(useListDepartments, listState([DEPARTMENT]));
  wireQuery(useListPositions, listState([POSITION]));
  wireQuery(useListLocations, listState([LOCATION]));
});

describe("EmployeeFormDialog", () => {
  it("submits the pinned CreateEmployee shape and closes", async () => {
    const user = userEvent.setup();
    createEmployee.mockResolvedValue({ data: { status: 201 } });
    renderDialog();

    await user.type(await screen.findByLabelText("First name"), "Alice");
    await user.type(screen.getByLabelText("Last name"), "Adams");
    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Hire date"), "2026-01-15");

    await user.click(screen.getByRole("combobox", { name: "Department" }));
    await user.click(await screen.findByRole("option", { name: "Engineering" }));
    await user.click(screen.getByRole("combobox", { name: "Position" }));
    await user.click(await screen.findByRole("option", { name: "Backend Engineer" }));
    await user.click(screen.getByRole("combobox", { name: "Location" }));
    await user.click(await screen.findByRole("option", { name: "Ho Chi Minh Office" }));
    await user.click(screen.getByRole("combobox", { name: "Manager (optional)" }));
    await user.click(await screen.findByRole("option", { name: "Morgan Reed" }));

    await user.click(screen.getByRole("button", { name: /create employee/i }));

    await waitFor(() =>
      expect(createEmployee).toHaveBeenCalledWith({
        data: {
          first_name: "Alice",
          last_name: "Adams",
          email: "alice@example.com",
          phone: null,
          department_id: DEPARTMENT.id,
          position_id: POSITION.id,
          location_id: LOCATION.id,
          manager_id: MANAGER.id,
          employment_type: "full_time",
          hired_at: "2026-01-15",
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Employee created");
  });

  it("blocks submission when required fields are missing", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("button", { name: /create employee/i }));

    expect(await screen.findByText("First name is required")).toBeInTheDocument();
    expect(createEmployee).not.toHaveBeenCalled();
  });

  it("renders a 409 duplicate email as a visible field error and keeps the dialog open (AC12)", async () => {
    const user = userEvent.setup();
    createEmployee.mockRejectedValue(
      apiError(409, "employee_email_taken", "Employee email already taken in this tenant"),
    );
    renderDialog();

    await user.type(await screen.findByLabelText("First name"), "Alice");
    await user.type(screen.getByLabelText("Last name"), "Adams");
    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Hire date"), "2026-01-15");
    await user.click(screen.getByRole("combobox", { name: "Department" }));
    await user.click(await screen.findByRole("option", { name: "Engineering" }));
    await user.click(screen.getByRole("combobox", { name: "Position" }));
    await user.click(await screen.findByRole("option", { name: "Backend Engineer" }));
    await user.click(screen.getByRole("combobox", { name: "Location" }));
    await user.click(await screen.findByRole("option", { name: "Ho Chi Minh Office" }));
    await user.click(screen.getByRole("button", { name: /create employee/i }));

    expect(
      await screen.findByText("An employee with this email already exists."),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("Employee email already taken in this tenant");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("edits profile fields only and sends the UpdateEmployee shape", async () => {
    const user = userEvent.setup();
    updateEmployee.mockResolvedValue({ data: { status: 200 } });
    renderDialog({ edit: true });

    const firstName = await screen.findByLabelText("First name");
    expect(firstName).toHaveValue("Morgan");
    // Assignment fields are not part of the pinned PATCH scope.
    expect(screen.queryByLabelText("Hire date")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Department" })).not.toBeInTheDocument();

    await user.clear(firstName);
    await user.type(firstName, "Morganne");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(updateEmployee).toHaveBeenCalledWith({
        id: MANAGER.id,
        data: {
          first_name: "Morganne",
          last_name: "Reed",
          email: "morgan@example.com",
          phone: null,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Employee updated");
  });
});
