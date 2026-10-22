/**
 * Run detail component tests (T10 / AC14) — the generated api-client is
 * mocked; the real component, labels and status gates run. The finalize button
 * must be disabled outside `calculated`, and the payslip expansion must expose
 * the inspectable deduction lines.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunDetail } from "@/features/payroll/components/RunDetail";
import type {
  Employee,
  PayrollConfig,
  PayrollRun,
  Payslip,
  Timesheet,
} from "@/features/payroll/types";

const authState = vi.hoisted(() => ({ role: "finance" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => ({
    user: {
      id: "00000000-0000-4000-8000-0000000000f1",
      tenant_id: "00000000-0000-4000-8000-000000000001",
      email: "finance@example.test",
      role: authState.role,
      employee_id: null,
      active: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getGetPayrollConfigQueryKey: () => ["/api/v1/payroll/config"],
  getGetPayrollRunQueryKey: (id: string) => [`/api/v1/payroll/runs/${id}`],
  getListPayrollDeductionsQueryKey: () => ["/api/v1/payroll/deductions"],
  getListPayrollRunsQueryKey: () => ["/api/v1/payroll/runs"],
  getListMyPayslipsQueryKey: () => ["/api/v1/payroll/payslips"],
  getListRunPayslipsQueryKey: (id: string) => [`/api/v1/payroll/runs/${id}/payslips`],
  getListRunTimesheetsQueryKey: (id: string) => [`/api/v1/payroll/runs/${id}/timesheets`],
  getListSalariesQueryKey: () => ["/api/v1/payroll/salaries"],
  useCreatePayrollRun: vi.fn(),
  useCreateRunBonus: vi.fn(),
  useFinalizePayrollRun: vi.fn(),
  useGetPayrollConfig: vi.fn(),
  useGetPayrollRun: vi.fn(),
  useListEmployees: vi.fn(),
  useListPayrollRuns: vi.fn(),
  useListRunPayslips: vi.fn(),
  useListRunTimesheets: vi.fn(),
  useRecalculatePayrollRun: vi.fn(),
  useUpdatePayrollConfig: vi.fn(),
}));

import {
  useCreateRunBonus,
  useFinalizePayrollRun,
  useGetPayrollConfig,
  useGetPayrollRun,
  useListEmployees,
  useListRunPayslips,
  useListRunTimesheets,
  useRecalculatePayrollRun,
} from "@workpulse/api-client";

const CONFIG: PayrollConfig = {
  tenant_id: "00000000-0000-4000-8000-000000000001",
  currency: "USD",
  standard_monthly_hours: 160,
  overtime_multiplier: 1.5,
  updated_at: "2026-09-01T00:00:00Z",
};

const EMPLOYEE: Employee = {
  id: "00000000-0000-4000-8000-0000000000e1",
  tenant_id: "00000000-0000-4000-8000-000000000001",
  employee_code: "EMP-00001",
  first_name: "Avery",
  last_name: "Stone",
  email: "avery.stone@example.test",
  phone: null,
  department_id: "00000000-0000-4000-8000-0000000000d1",
  position_id: "00000000-0000-4000-8000-0000000000d2",
  location_id: "00000000-0000-4000-8000-0000000000d3",
  manager_id: null,
  employment_type: "full_time",
  status: "active",
  hired_at: "2026-01-05",
  terminated_at: null,
  created_at: "2026-01-05T00:00:00Z",
  updated_at: "2026-01-05T00:00:00Z",
};

const BASE_RUN: PayrollRun = {
  id: "00000000-0000-4000-8000-0000000000a1",
  period: "2026-09",
  status: "calculated",
  employee_count: 1,
  skipped_count: 0,
  skipped: [],
  totals: {
    base: 3000,
    overtime_pay: 100,
    bonuses: 50,
    deductions: 305.5,
    net: 2844.5,
  },
  failed_code: null,
  calculated_at: "2026-10-01T10:00:00Z",
  finalized_at: null,
  supersedes_run_id: null,
  superseded_by_run_id: null,
  created_by: "00000000-0000-4000-8000-0000000000f1",
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
};

const PAYSLIP: Payslip = {
  id: "00000000-0000-4000-8000-0000000000c1",
  run_id: BASE_RUN.id,
  employee_id: EMPLOYEE.id,
  period: "2026-09",
  status: "provisional",
  base: 3000,
  overtime_pay: 100,
  bonuses: 50,
  deductions: [
    {
      code: "TAX",
      name: "Withholding",
      kind: "percent_of_base",
      value: 10,
      amount: 300,
    },
    {
      code: "FIXED",
      name: "Equipment",
      kind: "fixed_amount",
      value: 5.5,
      amount: 5.5,
    },
  ],
  net: 2844.5,
  issued_at: null,
};

const TIMESHEET: Timesheet = {
  employee_id: EMPLOYEE.id,
  period: "2026-09",
  worked_hours: 168,
  overtime_hours: 4,
  leave_days: 0,
  attendance_days: 21,
};

function queryState(data: unknown) {
  return {
    data: { data, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

function pagedState(items: unknown[]) {
  return queryState({
    items,
    page: { page: 1, page_size: 100, total_items: items.length, total_pages: 1 },
  });
}

function mutationStub() {
  return { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };
}

function renderDetail(runId: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RunDetail runId={runId} />
    </QueryClientProvider>,
  );
}

function wireRun(run: PayrollRun) {
  vi.mocked(useGetPayrollRun).mockReturnValue(queryState(run) as never);
  vi.mocked(useGetPayrollConfig).mockReturnValue(queryState(CONFIG) as never);
  vi.mocked(useListEmployees).mockReturnValue(pagedState([EMPLOYEE]) as never);
  vi.mocked(useListRunTimesheets).mockReturnValue(pagedState([TIMESHEET]) as never);
  vi.mocked(useListRunPayslips).mockReturnValue(pagedState([PAYSLIP]) as never);
  vi.mocked(useFinalizePayrollRun).mockReturnValue(mutationStub() as never);
  vi.mocked(useRecalculatePayrollRun).mockReturnValue(mutationStub() as never);
  vi.mocked(useCreateRunBonus).mockReturnValue(mutationStub() as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  authState.role = "finance";
});

describe("RunDetail", () => {
  it("enables finalize for a calculated run and exposes payslip deduction lines", async () => {
    wireRun(BASE_RUN);
    const user = userEvent.setup();

    renderDetail(BASE_RUN.id);

    expect(screen.getByRole("heading", { name: "September 2026" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Finalize run" })).toBeEnabled();
    expect(screen.getAllByText("$2,844.50").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Show payslip lines for Avery Stone" }));

    expect(screen.getByText("Deduction lines")).toBeInTheDocument();
    expect(screen.getByText("TAX")).toBeInTheDocument();
    expect(screen.getByText("$300.00")).toBeInTheDocument();
    expect(screen.getByText(/Fixed amount: \$5\.50/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Hide payslip lines for Avery Stone" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("disables finalize while the run is queued and explains why", () => {
    wireRun({
      ...BASE_RUN,
      status: "queued",
      totals: null,
      calculated_at: null,
    });

    renderDetail(BASE_RUN.id);

    expect(screen.getByRole("button", { name: "Finalize run" })).toBeDisabled();
    expect(
      screen.getByText("Finalize becomes available once the calculation finishes."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recalculate" })).not.toBeInTheDocument();
    expect(
      screen.getByText("Timesheets appear after the calculation finishes."),
    ).toBeInTheDocument();
  });

  it("offers recalculation on a failed run but keeps finalize disabled", () => {
    wireRun({
      ...BASE_RUN,
      status: "failed",
      totals: null,
      calculated_at: null,
      failed_code: "payroll_calculation_failed",
    });

    renderDetail(BASE_RUN.id);

    expect(screen.getByText("Calculation failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Recalculate" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Finalize run" })).toBeDisabled();
    expect(screen.getByText("Recalculate the run to produce timesheets.")).toBeInTheDocument();
  });

  it("shows HR the run read-only", () => {
    authState.role = "hr";
    wireRun(BASE_RUN);

    renderDetail(BASE_RUN.id);

    expect(screen.queryByRole("button", { name: "Finalize run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recalculate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add bonus" })).not.toBeInTheDocument();
    expect(
      screen.getByText("View only — Finance, Admin and Super Admin run payroll."),
    ).toBeInTheDocument();
  });

  it("does not fetch payroll data for a role that is being redirected", () => {
    authState.role = "employee";
    wireRun(BASE_RUN);

    renderDetail(BASE_RUN.id);

    expect(useGetPayrollRun).toHaveBeenCalledWith(
      BASE_RUN.id,
      expect.objectContaining({
        query: expect.objectContaining({ enabled: false }),
      }),
    );
    expect(useGetPayrollConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        query: expect.objectContaining({ enabled: false }),
      }),
    );
  });
});
