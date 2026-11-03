/**
 * Shell navigation tests — the grouped sidebar (desktop + collapsed), the
 * mobile sheet, and the ⌘K palette. Auth, the viewport and the router are
 * mocked; destinations and their role gates are the real catalog.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { User } from "@workpulse/api-client/index.schemas";

import { activeNavHref, NAV_GROUPS, NAV_ITEMS, visibleNavGroups } from "@/components/destinations";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { useAuth } from "@/features/auth/components/AuthGate";
import { AppSidebar } from "@/features/shell/AppSidebar";
import { CommandPalette } from "@/features/shell/CommandPalette";

const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
}));

const mobileMock = vi.hoisted(() => ({ mobile: false }));
const setTheme = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  usePathname: () => "/attendance",
  useRouter: () => routerMocks,
}));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => mobileMock.mobile }));
vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light", setTheme }),
}));
vi.mock("@/features/auth/components/AuthGate", () => ({ useAuth: vi.fn() }));

const mockUseAuth = vi.mocked(useAuth);

const ADMIN: User = {
  id: "01900000-0000-7000-8000-0000000000a1",
  tenant_id: "01900000-0000-7000-8000-000000000001",
  email: "admin@workpulse.test",
  role: "admin",
  employee_id: null,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const WORKER: User = {
  ...ADMIN,
  id: "01900000-0000-7000-8000-0000000000a4",
  email: "worker@workpulse.test",
  role: "employee",
  employee_id: "01900000-0000-7000-8000-00000000a002",
};

beforeEach(() => {
  vi.clearAllMocks();
  mobileMock.mobile = false;
  mockUseAuth.mockReturnValue({ user: ADMIN });
});

describe("the nav catalog", () => {
  it("assigns every destination to exactly one group", () => {
    const grouped = NAV_GROUPS.flatMap((group) => group.items);
    expect(grouped).toHaveLength(NAV_ITEMS.length);
    expect(new Set(grouped.map((item) => item.key))).toEqual(
      new Set(NAV_ITEMS.map((item) => item.key)),
    );
  });

  it("keeps the approved group order", () => {
    expect(NAV_GROUPS.map((group) => group.key)).toEqual([
      "overview",
      "workforce",
      "payroll",
      "hiring",
      "insight",
      "administration",
    ]);
  });

  it("drops role-restricted items and any group left empty", () => {
    const employeeGroups = visibleNavGroups("employee");
    const employeeKeys = employeeGroups.flatMap((group) => group.items.map((item) => item.key));

    expect(employeeKeys).toContain("myPayslips");
    expect(employeeKeys).toContain("myDocuments");
    expect(employeeKeys).not.toContain("users");
    expect(employeeKeys).not.toContain("commandCenter");
    expect(employeeKeys).not.toContain("payroll");

    expect(
      visibleNavGroups("admin").flatMap((group) => group.items.map((item) => item.key)),
    ).toContain("users");
  });

  it("marks only the most specific item for a nested route", () => {
    expect(activeNavHref("/payroll/my-payslips/abc", NAV_ITEMS)).toBe("/payroll/my-payslips");
    expect(activeNavHref("/payroll/runs/abc", NAV_ITEMS)).toBe("/payroll");
  });
});

describe("AppSidebar", () => {
  it("renders grouped destinations and marks the active route", () => {
    render(
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>,
    );

    expect(screen.getByText("Workforce")).toBeInTheDocument();
    expect(screen.getByText("Administration")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Employees" })).toHaveAttribute("href", "/employees");
    expect(screen.getByRole("link", { name: "Attendance" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  });

  it("hides role-gated destinations from an employee", () => {
    mockUseAuth.mockReturnValue({ user: WORKER });
    render(
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>,
    );

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Command center" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "My payslips" })).toBeInTheDocument();
  });

  it("keeps every destination reachable while collapsed", () => {
    const { container } = render(
      <SidebarProvider defaultOpen={false}>
        <AppSidebar />
      </SidebarProvider>,
    );

    expect(container.querySelector('[data-state="collapsed"]')).not.toBeNull();
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
  });

  it("opens the mobile sheet, marks the active route and closes on navigation", async () => {
    mobileMock.mobile = true;
    const user = userEvent.setup();
    render(
      <SidebarProvider>
        <SidebarTrigger label="Toggle navigation" />
        <AppSidebar />
      </SidebarProvider>,
    );

    const trigger = screen.getByRole("button", { name: "Toggle navigation" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: "Employees" })).not.toBeInTheDocument();

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "Attendance" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    const employees = screen.getByRole("link", { name: "Employees" });
    employees.addEventListener("click", (event) => event.preventDefault(), { once: true });
    await user.click(employees);

    await waitFor(() => expect(trigger).toHaveAttribute("aria-expanded", "false"));
    expect(screen.queryByRole("link", { name: "Employees" })).not.toBeInTheDocument();
  });
});

describe("CommandPalette", () => {
  it("lists the role-visible destinations and navigates on Enter", async () => {
    const user = userEvent.setup();
    render(<CommandPalette />);

    await user.click(screen.getByRole("button", { name: /search/i }));
    await screen.findByRole("dialog");
    expect(await screen.findByRole("option", { name: "Employees" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Users" })).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Search pages…"), "employ");
    await user.keyboard("{Enter}");

    expect(routerMocks.push).toHaveBeenCalledWith("/employees");
  });

  it("never offers destinations the role cannot open", async () => {
    mockUseAuth.mockReturnValue({ user: WORKER });
    const user = userEvent.setup();
    render(<CommandPalette />);

    await user.click(screen.getByRole("button", { name: /search/i }));
    await screen.findByRole("dialog");

    expect(await screen.findByRole("option", { name: "My payslips" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Command center" })).not.toBeInTheDocument();
  });

  it("shows an empty state when nothing matches", async () => {
    const user = userEvent.setup();
    render(<CommandPalette />);

    await user.click(screen.getByRole("button", { name: /search/i }));
    await screen.findByRole("dialog");
    await user.type(screen.getByPlaceholderText("Search pages…"), "zzzzz");

    expect(await screen.findByText("No matching page.")).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    render(<CommandPalette />);

    await user.click(screen.getByRole("button", { name: /search/i }));
    await screen.findByRole("dialog");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
