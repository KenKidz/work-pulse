/**
 * Recruitment workspace guard — HR/Admin see every management tab, a Manager
 * sees only "My interviews", and Employees/Finance are redirected to the
 * dashboard. JobsTable is stubbed: this suite covers the shell and the access
 * rules only.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecruitmentWorkspace } from "@/features/recruitment/components/RecruitmentWorkspace";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  user: { role: "hr" as string },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => ({ user: mocks.user }),
}));

vi.mock("@/features/recruitment/components/JobsTable", () => ({
  JobsTable: () => <p>Jobs table</p>,
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.user = { role: "hr" };
});

describe("RecruitmentWorkspace", () => {
  it("shows every management tab to HR and lands on Jobs", () => {
    render(<RecruitmentWorkspace />);

    expect(screen.getByTestId("recruitment-workspace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Jobs" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Candidates" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Interviews" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "My interviews" })).toBeInTheDocument();
    expect(screen.getByText("Jobs table")).toBeInTheDocument();
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("gives a Manager only the assigned-interview tab", () => {
    mocks.user = { role: "manager" };
    render(<RecruitmentWorkspace />);

    expect(screen.getByRole("tab", { name: "My interviews" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Jobs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Candidates" })).not.toBeInTheDocument();
    expect(screen.getByText("No interviews to show here yet.")).toBeInTheDocument();
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it.each(["employee", "finance"])("redirects a %s session to the dashboard", async (role) => {
    mocks.user = { role };
    render(<RecruitmentWorkspace />);

    expect(screen.getByText("Checking access…")).toBeInTheDocument();
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(screen.queryByTestId("recruitment-workspace")).not.toBeInTheDocument();
  });
});
