/**
 * TerminateDialog tests — the confirm flow closes the open period, and the
 * pinned double-termination 409 renders a visible root error.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useTerminateEmployee } from "@workpulse/api-client";
import { toast } from "sonner";

import { TerminateDialog } from "@/features/employees/components/TerminateDialog";
import { todayISO } from "@/features/employees/components/dates";

import { apiError, wireMutation } from "./helpers";

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

const terminateEmployee = vi.fn<(variables: unknown) => Promise<unknown>>();

function renderDialog() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <TerminateDialog open onOpenChange={() => undefined} employeeId={EMPLOYEE_ID} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useTerminateEmployee, terminateEmployee);
});

describe("TerminateDialog", () => {
  it("requires a reason before submitting", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByRole("button", { name: "Terminate" }));

    expect(await screen.findByText("A reason is required")).toBeInTheDocument();
    expect(terminateEmployee).not.toHaveBeenCalled();
  });

  it("submits the pinned TerminationRequest and toasts", async () => {
    const user = userEvent.setup();
    terminateEmployee.mockResolvedValue({ data: { status: 200 } });
    renderDialog();

    await user.type(await screen.findByLabelText("Reason"), "Role eliminated");
    await user.click(screen.getByRole("button", { name: "Terminate" }));

    await waitFor(() =>
      expect(terminateEmployee).toHaveBeenCalledWith({
        id: EMPLOYEE_ID,
        data: {
          effective_date: todayISO(),
          reason: "Role eliminated",
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Employee terminated");
  });

  it("renders a 409 already-terminated as a visible root error", async () => {
    const user = userEvent.setup();
    terminateEmployee.mockRejectedValue(
      apiError(409, "employee_already_terminated", "Employee is already terminated"),
    );
    renderDialog();

    await user.type(await screen.findByLabelText("Reason"), "Role eliminated");
    await user.click(screen.getByRole("button", { name: "Terminate" }));

    expect(
      await screen.findByText("This employee has already been terminated."),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("Employee is already terminated");
  });
});
