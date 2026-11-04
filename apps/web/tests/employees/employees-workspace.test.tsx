/**
 * EmployeesWorkspace access guard — the directory is manager+ only and the
 * create action is HR+. A disallowed role is redirected before the table
 * mounts, so no employees query is issued (the 403 path never shows).
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { User } from "@workpulse/api-client/index.schemas";

import { useAuth } from "@/features/auth/components/AuthGate";
import { EmployeesWorkspace } from "@/features/employees/components/EmployeesWorkspace";

const routerMocks = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMocks,
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: vi.fn(),
}));

vi.mock("@/features/employees/components/EmployeesTable", () => ({
  EmployeesTable: () => <p>Directory table</p>,
}));

vi.mock("@/features/employees/components/NewEmployeeButton", () => ({
  NewEmployeeButton: () => <button type="button">New employee</button>,
}));

const mockUseAuth = vi.mocked(useAuth);

const BASE: User = {
  id: "01900000-0000-7000-8000-0000000000a1",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  email: "person@workpulse.test",
  role: "hr",
  employee_id: "01900000-0000-7000-8000-00000000a001",
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const HR: User = { ...BASE, email: "hr@workpulse.test" };
const MANAGER: User = { ...BASE, email: "manager@workpulse.test", role: "manager" };
const WORKER: User = { ...BASE, email: "worker@workpulse.test", role: "employee" };

beforeEach(() => {
  vi.clearAllMocks();
});

describe("EmployeesWorkspace", () => {
  it("renders the directory and the create action for HR", () => {
    mockUseAuth.mockReturnValue({ user: HR });
    render(<EmployeesWorkspace />);

    expect(screen.getByRole("heading", { name: "Employees" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New employee" })).toBeInTheDocument();
    expect(screen.getByText("Directory table")).toBeInTheDocument();
  });

  it("renders the directory without the create action for a manager", () => {
    mockUseAuth.mockReturnValue({ user: MANAGER });
    render(<EmployeesWorkspace />);

    expect(screen.getByText("Directory table")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New employee" })).not.toBeInTheDocument();
  });

  it("redirects a worker without mounting the directory", async () => {
    mockUseAuth.mockReturnValue({ user: WORKER });
    render(<EmployeesWorkspace />);

    expect(screen.getByText("Checking access…")).toBeInTheDocument();
    await waitFor(() => expect(routerMocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText("Directory table")).not.toBeInTheDocument();
  });
});
