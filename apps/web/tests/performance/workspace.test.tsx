/**
 * Performance workspace shell — every role with an own record or a team scope
 * gets Goals · Reviews · Skills; a role with neither gets the denied state.
 * The panels are stubbed: this suite covers the shell and its access rules.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import viMessages from "@/messages/vi";

import { PerformanceWorkspace } from "@/features/performance/components/PerformanceWorkspace";

const mocks = vi.hoisted(() => ({
  access: {
    user: {
      role: "manager",
      employee_id: "01900000-0000-7000-8000-000000000001" as string | null,
    },
    canViewTeam: true,
    canManageGoals: true,
    canRunReviews: true,
    canManageCatalog: false,
    canRecordSkills: true,
    canUseWorkspace: true,
  },
}));

vi.mock("@/features/performance/hooks/use-performance", () => ({
  usePerformanceAccess: () => mocks.access,
}));

vi.mock("@/features/performance/components/GoalsPanel", () => ({
  GoalsPanel: () => <p>Goals panel</p>,
}));

vi.mock("@/features/performance/components/ReviewsPanel", () => ({
  ReviewsPanel: () => <p>Reviews panel</p>,
}));

vi.mock("@/features/performance/components/SkillsPanel", () => ({
  SkillsPanel: () => <p>Skills panel</p>,
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.access = {
    user: {
      role: "manager",
      employee_id: "01900000-0000-7000-8000-000000000001" as string | null,
    },
    canViewTeam: true,
    canManageGoals: true,
    canRunReviews: true,
    canManageCatalog: false,
    canRecordSkills: true,
    canUseWorkspace: true,
  };
});

describe("PerformanceWorkspace", () => {
  it("shows Goals, Reviews and Skills to a manager and lands on Goals", async () => {
    const user = userEvent.setup();
    render(<PerformanceWorkspace />);

    expect(screen.getByTestId("performance-workspace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Goals" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Reviews" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Skills" })).toBeInTheDocument();
    expect(screen.getByText("Goals panel")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Reviews" }));
    expect(await screen.findByText("Reviews panel")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Skills" }));
    expect(await screen.findByText("Skills panel")).toBeInTheDocument();
  });

  it("gives an employee with a linked record their own tabs", () => {
    mocks.access = {
      user: { role: "employee", employee_id: "01900000-0000-7000-8000-000000000002" },
      canViewTeam: false,
      canManageGoals: true,
      canRunReviews: false,
      canManageCatalog: false,
      canRecordSkills: true,
      canUseWorkspace: true,
    };
    render(<PerformanceWorkspace />);

    expect(screen.getByRole("tab", { name: "Goals" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Reviews" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Skills" })).toBeInTheDocument();
    expect(screen.getByText("Goals panel")).toBeInTheDocument();
  });

  it.each(["employee", "finance"])(
    "shows the denied state to a %s session with no employee link",
    (role) => {
      mocks.access = {
        user: { role, employee_id: null },
        canViewTeam: false,
        canManageGoals: role === "employee",
        canRunReviews: false,
        canManageCatalog: false,
        canRecordSkills: role === "employee",
        canUseWorkspace: false,
      };
      render(<PerformanceWorkspace />);

      expect(
        screen.getByText(
          "Your account isn't linked to an employee record, so there is nothing personal to show here. Ask HR to link your account.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("performance-workspace")).not.toBeInTheDocument();
      expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    },
  );

  it("has the performance surface translated in Vietnamese", () => {
    expect(viMessages.performance.pageTitle).toBe("Hiệu suất");
    expect(viMessages.performance.tabs.goals).toBe("Mục tiêu");
    expect(viMessages.performance.goals.loading).toBe("Đang tải mục tiêu…");
    expect(viMessages.performance.goalForm.title).toBe("Mục tiêu");
    expect(viMessages.performance.actions.newGoal).toBe("Mục tiêu mới");
  });
});
