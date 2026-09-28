/**
 * TransferDialog tests (AC12) — the happy path appends a new employment
 * period (omitted refs inherit), and server 409/422 failures render visible
 * errors: archived target at root, effective-date issues on the field.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useListEmployees,
  useTransferEmployee,
  useListDepartments,
  useListPositions,
  useListLocations,
} from "@workpulse/api-client";
import { toast } from "sonner";

import { TransferDialog } from "@/features/employees/components/TransferDialog";
import { todayISO } from "@/features/employees/components/dates";

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

const EMPLOYEE_ID = "01900000-0000-7000-8000-00000000a001";
const CURRENT_DEPARTMENT_ID = "01900000-0000-7000-8000-000000000d01";

const DEPARTMENT = {
  id: "01900000-0000-7000-8000-000000000d02",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  name: "People Ops",
  parent_department_id: null,
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

const transferEmployee = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <TransferDialog
        open
        onOpenChange={() => undefined}
        employeeId={EMPLOYEE_ID}
        currentDepartmentId={CURRENT_DEPARTMENT_ID}
      />
    </QueryClientProvider>,
  );
  return { invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useTransferEmployee, transferEmployee);
  wireQuery(useListDepartments, listState([DEPARTMENT]));
  wireQuery(useListPositions, listState([]));
  wireQuery(useListLocations, listState([]));
  // Manager options come from an active-employees query.
  wireQuery(useListEmployees, listState([MANAGER]));
});

describe("TransferDialog", () => {
  it("records a transfer with inherited refs and today's effective date", async () => {
    const user = userEvent.setup();
    transferEmployee.mockResolvedValue({ data: { status: 200 } });
    const { invalidateSpy } = renderDialog();

    // Department starts at the current assignment; everything else "keep".
    await user.click(await screen.findByRole("button", { name: /record transfer/i }));

    await waitFor(() =>
      expect(transferEmployee).toHaveBeenCalledWith({
        id: EMPLOYEE_ID,
        data: {
          department_id: CURRENT_DEPARTMENT_ID,
          // Omitted refs inherit the current period (undefined is dropped
          // before the request by JSON.stringify).
          position_id: undefined,
          location_id: undefined,
          manager_id: undefined,
          effective_from: todayISO(),
          reason: null,
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Transfer recorded");
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: [`/api/v1/employees/${EMPLOYEE_ID}`],
      }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: [`/api/v1/employees/${EMPLOYEE_ID}/employments`],
    });
  });

  it("sends a chosen department and manager", async () => {
    const user = userEvent.setup();
    transferEmployee.mockResolvedValue({ data: { status: 200 } });
    renderDialog();

    await user.click(await screen.findByRole("combobox", { name: "Department" }));
    await user.click(await screen.findByRole("option", { name: "People Ops" }));
    await user.click(screen.getByRole("combobox", { name: "Manager" }));
    await user.click(await screen.findByRole("option", { name: "Morgan Reed" }));
    await user.click(screen.getByRole("button", { name: /record transfer/i }));

    await waitFor(() =>
      expect(transferEmployee).toHaveBeenCalledWith(
        expect.objectContaining({
          data: expect.objectContaining({
            department_id: DEPARTMENT.id,
            manager_id: MANAGER.id,
          }),
        }),
      ),
    );
  });

  it("renders a 409 archived target as a visible root error (AC12)", async () => {
    const user = userEvent.setup();
    transferEmployee.mockRejectedValue(
      apiError(409, "transfer_target_archived", "Assignment target is archived"),
    );
    renderDialog();

    await user.click(await screen.findByRole("button", { name: /record transfer/i }));

    expect(
      await screen.findByText("The selected department, position or location is archived."),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("Assignment target is archived");
    // The dialog stays open so the user can pick a different target.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("renders a 422 invalid effective date on the date field", async () => {
    const user = userEvent.setup();
    transferEmployee.mockRejectedValue(
      apiError(422, "employee_transfer_effective_invalid", "Transfer effective date is invalid"),
    );
    renderDialog();

    await user.click(await screen.findByRole("button", { name: /record transfer/i }));

    expect(
      await screen.findByText(/The effective date must be within employment/i),
    ).toBeInTheDocument();
  });
});
