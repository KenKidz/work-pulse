/**
 * Employee detail shell — the record on Overview and the documents panel on a
 * second tab. The panels are stubbed; this suite covers the shell and the id
 * it hands to the documents surface.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmployeeDetailWorkspace } from "@/features/employees/components/EmployeeDetailWorkspace";
import viMessages from "@/messages/vi";

vi.mock("@/features/employees/components/EmployeeDetailCard", () => ({
  EmployeeDetailCard: ({ id }: { id: string }) => <p>Overview for {id}</p>,
}));

vi.mock("@/features/documents/components/DocumentsPanel", () => ({
  DocumentsPanel: ({ employeeId }: { employeeId: string }) => <p>Documents for {employeeId}</p>,
}));

const EMPLOYEE_ID = "01900000-0000-7000-8000-0000000000a1";

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
});
