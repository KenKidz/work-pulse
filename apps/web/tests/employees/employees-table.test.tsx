/**
 * EmployeesTable feature tests (AC12) — the generated api-client layer is
 * mocked; hooks, error mapping, table states and the create dialog are the
 * real thing. Covers loading/empty/error, the filter bar, pagination, the
 * create happy path and the 409 duplicate-email visible error state.
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
  useGetCurrentEmployee,
} from "@workpulse/api-client";
import { toast } from "sonner";

import {
  EMPLOYEES_PAGE_SIZE,
  EmployeesTable,
} from "@/features/employees/components/EmployeesTable";
import { NewEmployeeButton } from "@/features/employees/components/NewEmployeeButton";
import { useEmployeeDirectory } from "@/features/employees/hooks/use-employee-directory";
import type { EmployeeSummary } from "@/features/employees/types";

import { apiError, listState, wireMutation, wireQuery } from "./helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => ({
    user: {
      id: "01900000-0000-7000-8000-0000000000b1",
      tenant_id: "01900000-0000-7000-8000-000000000001",
      email: "manager@workpulse.test",
      role: "manager",
      employee_id: "01900000-0000-7000-8000-00000000a001",
      active: true,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    },
  }),
}));

vi.mock("@/features/employees/hooks/use-employee-directory", () => ({
  useEmployeeDirectory: vi.fn(),
}));

vi.mock("@workpulse/api-client", () => ({
  // Query keys used by the feature invalidation helpers.
  getListEmployeesQueryKey: () => ["/api/v1/employees"],
  getGetEmployeeQueryKey: (id: string) => [`/api/v1/employees/${id}`],
  getListEmploymentsQueryKey: (id: string) => [`/api/v1/employees/${id}/employments`],
  getListDepartmentsQueryKey: () => ["/api/v1/departments"],
  getListPositionsQueryKey: () => ["/api/v1/positions"],
  getListLocationsQueryKey: () => ["/api/v1/locations"],
  getGetCurrentEmployeeQueryKey: () => ["/api/v1/employees/me"],
  // Hooks.
  useListEmployees: vi.fn(),
  useGetCurrentEmployee: vi.fn(),
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

const ALICE: EmployeeSummary = {
  id: "01900000-0000-7000-8000-00000000a001",
  employee_code: "EMP-00001",
  first_name: "Alice",
  last_name: "Adams",
  email: "alice@example.com",
  department_id: DEPARTMENT.id,
  position_id: POSITION.id,
  location_id: LOCATION.id,
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2026-01-15",
  terminated_at: null,
};

const createEmployee = vi.fn<(variables: unknown) => Promise<unknown>>();

/** All list calls carrying the table's page size (shared mocked hook). */
function tableCalls() {
  return vi
    .mocked(useListEmployees)
    .mock.calls.map(([params]) => params)
    .filter((params) => params?.page_size === EMPLOYEES_PAGE_SIZE);
}

function renderTable() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <EmployeesTable />
      <NewEmployeeButton />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useCreateEmployee, createEmployee);
  wireMutation(useUpdateEmployee, vi.fn());
  wireQuery(useListEmployees, listState([ALICE]));
  wireQuery(useEmployeeDirectory, {
    data: [ALICE],
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  wireQuery(useGetCurrentEmployee, {
    data: { status: 200, data: ALICE },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });
  wireQuery(useListDepartments, listState([DEPARTMENT]));
  wireQuery(useListPositions, listState([POSITION]));
  wireQuery(useListLocations, listState([LOCATION]));
});

describe("EmployeesTable", () => {
  it("renders fetched employees with a link to the detail page", async () => {
    renderTable();

    const link = await screen.findByRole("link", { name: "Alice Adams" });
    expect(link).toHaveAttribute("href", `/employees/${ALICE.id}`);
    expect(screen.getByText("EMP-00001")).toBeInTheDocument();
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
    expect(screen.getByText("Full-time")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("resolves the manager column for a manager viewing direct reports", async () => {
    const report = {
      ...ALICE,
      id: "01900000-0000-7000-8000-00000000a002",
      employee_code: "EMP-00002",
      first_name: "Bob",
      last_name: "Brown",
      email: "bob@example.com",
      manager_id: ALICE.id,
    };
    wireQuery(useListEmployees, listState([report]));
    wireQuery(useEmployeeDirectory, {
      data: [ALICE, report],
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderTable();

    expect(await screen.findByRole("link", { name: "Bob Brown" })).toBeInTheDocument();
    expect(screen.getByText("Alice Adams")).toBeInTheDocument();
  });

  it("names and hints the horizontal scroll region", async () => {
    renderTable();

    expect(
      await screen.findByRole("region", {
        name: "Employees. Scroll horizontally to read all columns.",
      }),
    ).toHaveAttribute("tabindex", "0");
    expect(screen.getByText("Swipe sideways to see every employee field.")).toBeInTheDocument();
  });

  it("shows a loading state", () => {
    wireQuery(useListEmployees, listState([], { data: undefined, isPending: true }));

    renderTable();

    expect(screen.getByText("Loading employees…")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    wireQuery(useListEmployees, listState([]));

    renderTable();

    expect(
      await screen.findByText(/No employees yet. Create the first employee record/i),
    ).toBeInTheDocument();
  });

  it("offers Clear filters when the empty result comes from active filters", async () => {
    const user = userEvent.setup();
    wireQuery(useListEmployees, listState([]));
    renderTable();

    await user.type(await screen.findByRole("textbox", { name: "Search employees" }), "nobody");
    expect(await screen.findByText(/No employees match these filters/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "Search employees" })).toHaveValue("");
    });
    await waitFor(() =>
      expect(useListEmployees).toHaveBeenCalledWith(
        expect.objectContaining({ q: undefined, page: 1 }),
      ),
    );
  });

  it("shows the result count in the table footer", async () => {
    renderTable();

    expect(await screen.findByText("Showing 1 employee")).toBeInTheDocument();
  });

  it("shows an error state with a retry action", async () => {
    wireQuery(
      useListEmployees,
      listState([], { data: undefined, isError: true, error: new Error("boom") }),
    );

    renderTable();

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load employees.");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("applies the q filter and the status filter to the list query", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.type(await screen.findByRole("textbox", { name: "Search employees" }), "ali");

    await waitFor(() =>
      expect(useListEmployees).toHaveBeenCalledWith(expect.objectContaining({ q: "ali" })),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Active" }));

    await waitFor(() =>
      expect(useListEmployees).toHaveBeenCalledWith(
        expect.objectContaining({ status: "active", page: 1 }),
      ),
    );
  });

  it("debounces the search input: one request per pause, none per keystroke", async () => {
    const user = userEvent.setup();
    renderTable();

    await user.type(await screen.findByRole("textbox", { name: "Search employees" }), "ali");

    await waitFor(() =>
      expect(useListEmployees).toHaveBeenCalledWith(expect.objectContaining({ q: "ali" })),
    );

    // The committed query only ever saw the final value — no per-keystroke
    // requests for "a" or "al" on the way there.
    const committed = tableCalls()
      .map((params) => params?.q)
      .filter((q) => q !== undefined);
    expect(committed).toEqual(["ali"]);
  });

  it("clearing a filter drops the parameter instead of sending null (422 regression)", async () => {
    // The generated client serializes null as the literal "null" query value;
    // the API 422s parsing it as an enum, so "All statuses" must omit status.
    const user = userEvent.setup();
    renderTable();

    await user.click(await screen.findByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "Active" }));
    await waitFor(() =>
      expect(useListEmployees).toHaveBeenCalledWith(expect.objectContaining({ status: "active" })),
    );

    await user.click(screen.getByRole("combobox", { name: "Filter by status" }));
    await user.click(await screen.findByRole("option", { name: "All statuses" }));

    await waitFor(() => {
      // The dialog's manager picker shares the mocked hook — take the most
      // recent call for the table query (page_size 20), not any call.
      const tableCalls = vi
        .mocked(useListEmployees)
        .mock.calls.map(([params]) => params)
        .filter((params) => params?.page_size === EMPLOYEES_PAGE_SIZE);
      const lastParams = tableCalls[tableCalls.length - 1];
      expect(lastParams).toEqual(expect.objectContaining({ status: undefined }));
      expect(Object.values(lastParams ?? {})).not.toContain(null);
    });
  });

  it("paginates", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 3 }, (_, index) => ({
      ...ALICE,
      id: `01900000-0000-7000-8000-00000000a0${index}`,
      first_name: `Emp${index}`,
    }));
    wireQuery(
      useListEmployees,
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
      expect(useListEmployees).toHaveBeenCalledWith(expect.objectContaining({ page: 2 })),
    );
  });

  it("creates an employee and invalidates the list", async () => {
    const user = userEvent.setup();
    createEmployee.mockResolvedValue({ data: { data: ALICE, status: 201 } });
    const { invalidateSpy } = renderTable();

    await user.click(await screen.findByRole("button", { name: /new employee/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    await user.type(screen.getByLabelText("First name"), "Alice");
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
          manager_id: null,
          employment_type: "full_time",
          hired_at: "2026-01-15",
        },
      }),
    );
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["/api/v1/employees"] }),
    );
    expect(toast.success).toHaveBeenCalledWith("Employee created");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("renders a 409 duplicate email as a visible field error (AC12)", async () => {
    const user = userEvent.setup();
    createEmployee.mockRejectedValue(
      apiError(409, "employee_email_taken", "Employee email already taken in this tenant"),
    );
    renderTable();

    await user.click(await screen.findByRole("button", { name: /new employee/i }));
    await user.type(screen.getByLabelText("First name"), "Alice");
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
    // The dialog stays open so the user can fix the email.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
