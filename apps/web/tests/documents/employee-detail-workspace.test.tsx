/**
 * Employee detail shell — the record on Overview and the documents panel on a
 * second tab. The panels are stubbed; this suite covers the shell and the id
 * it hands to the documents surface.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { User } from "@workpulse/api-client/index.schemas";

import { useAuth } from "@/features/auth/components/AuthGate";
import { EmployeeDetailWorkspace } from "@/features/employees/components/EmployeeDetailWorkspace";
import viMessages from "@/messages/vi";

const routerMocks = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMocks,
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: vi.fn(),
}));

vi.mock("@/features/employees/components/EmployeeDetailCard", () => ({
  EmployeeDetailCard: ({ id }: { id: string }) => <p>Overview for {id}</p>,
}));

vi.mock("@/features/documents/components/DocumentsPanel", () => ({
  DocumentsPanel: ({ employeeId }: { employeeId: string }) => <p>Documents for {employeeId}</p>,
}));

const mockUseAuth = vi.mocked(useAuth);

const HR: User = {
  id: "01900000-0000-7000-8000-0000000000b1",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  email: "hr@workpulse.test",
  role: "hr",
  employee_id: "01900000-0000-7000-8000-00000000a001",
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const WORKER: User = {
  ...HR,
  id: "01900000-0000-7000-8000-0000000000b2",
  email: "worker@workpulse.test",
  role: "employee",
};

const EMPLOYEE_ID = "01900000-0000-7000-8000-0000000000a1";

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({ user: HR });
});

describe("EmployeeDetailWorkspace", () => {
  it("opens on Overview and switches to the employee's documents", async () => {
    const user = userEvent.setup();
    render(<EmployeeDetailWorkspace id={EMPLOYEE_ID} />);

    expect(screen.getByTestId("employee-detail-workspace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Documents" })).toBeInTheDocument();
    expect(screen.getByText(`Overview for ${EMPLOYEE_ID}`)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Documents" }));
    expect(await screen.findByText(`Documents for ${EMPLOYEE_ID}`)).toBeInTheDocument();
  });

  it("has the tab labels translated in Vietnamese", () => {
    expect(viMessages.employees.detail.tabs.overview).toBe("Tổng quan");
    expect(viMessages.employees.detail.tabs.documents).toBe("Tài liệu");
  });

  it("redirects a role that cannot read employees to the dashboard", async () => {
    mockUseAuth.mockReturnValue({ user: WORKER });
    render(<EmployeeDetailWorkspace id={EMPLOYEE_ID} />);

    expect(screen.getByText("Checking access…")).toBeInTheDocument();
    await waitFor(() => expect(routerMocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByText(`Overview for ${EMPLOYEE_ID}`)).not.toBeInTheDocument();
  });
});
