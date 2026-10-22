import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "@/features/auth/components/AuthGate";
import type { User } from "@workpulse/api-client/index.schemas";

import { MainNav } from "@/components/main-nav";
import { MobileNav } from "@/components/mobile-nav";
import { activeNavHref, NAV_ITEMS } from "@/components/destinations";

vi.mock("next/navigation", () => ({
  usePathname: () => "/attendance",
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: vi.fn(),
}));

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

const HR: User = {
  ...ADMIN,
  id: "01900000-0000-7000-8000-0000000000a2",
  email: "hr@workpulse.test",
  role: "hr",
};

const MANAGER: User = {
  ...ADMIN,
  id: "01900000-0000-7000-8000-0000000000a3",
  email: "manager@workpulse.test",
  role: "manager",
  employee_id: "01900000-0000-7000-8000-00000000a001",
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
  mockUseAuth.mockReturnValue({ user: ADMIN });
});

describe("MobileNav", () => {
  it("opens the drawer with every primary destination", async () => {
    const user = userEvent.setup();
    render(<MobileNav />);

    const trigger = screen.getByRole("button", { name: "Open navigation menu" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveClass("size-11", "md:hidden");
    expect(screen.getByRole("button", { name: "Close" })).toHaveClass("size-11");
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: "Employees" })).toHaveAttribute("href", "/employees");
    expect(screen.getByRole("link", { name: "Attendance" })).toHaveAttribute("href", "/attendance");
    expect(screen.getByRole("link", { name: "Command center" })).toHaveAttribute(
      "href",
      "/command-center",
    );
    expect(screen.getByRole("link", { name: "Organization" })).toHaveAttribute(
      "href",
      "/settings/organization",
    );
  });

  it("shows the Command center destination to managers and hides it from employees", async () => {
    const user = userEvent.setup();
    mockUseAuth.mockReturnValue({ user: MANAGER });
    const { unmount } = render(<MobileNav />);
    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
    expect(screen.getByRole("link", { name: "Command center" })).toHaveAttribute(
      "href",
      "/command-center",
    );

    unmount();
    mockUseAuth.mockReturnValue({ user: WORKER });
    render(<MobileNav />);
    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));
    expect(screen.queryByRole("link", { name: "Command center" })).not.toBeInTheDocument();
  });

  it("shows the admin-only Users destination for admins", async () => {
    const user = userEvent.setup();
    render(<MobileNav />);

    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));

    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("href", "/settings/users");
  });

  it("hides the admin-only Users destination from non-admins", async () => {
    mockUseAuth.mockReturnValue({ user: HR });
    const user = userEvent.setup();
    render(<MobileNav />);

    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Employees" })).toBeInTheDocument();
  });

  it("marks the current destination as active", async () => {
    const user = userEvent.setup();
    render(<MobileNav />);

    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));

    expect(screen.getByRole("link", { name: "Attendance" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Employees" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Organization" })).not.toHaveAttribute("aria-current");
  });

  it("closes the drawer after selecting a destination", async () => {
    const user = userEvent.setup();
    render(<MobileNav />);

    const trigger = screen.getByRole("button", { name: "Open navigation menu" });
    await user.click(trigger);
    const employeesLink = screen.getByRole("link", { name: "Employees" });
    employeesLink.addEventListener("click", (event) => event.preventDefault(), { once: true });
    await user.click(employeesLink);

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("link", { name: "Employees" })).not.toBeInTheDocument();
  });
});

describe("MainNav", () => {
  it("marks the most specific destination for a nested payroll route", () => {
    expect(activeNavHref("/payroll/my-payslips/abc", NAV_ITEMS)).toBe("/payroll/my-payslips");
    expect(activeNavHref("/payroll/runs/abc", NAV_ITEMS)).toBe("/payroll");
  });

  it("preserves the desktop navigation destinations", () => {
    render(<MainNav />);

    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: "Employees" })).toHaveAttribute("href", "/employees");
    expect(screen.getByRole("link", { name: "Attendance" })).toHaveAttribute("href", "/attendance");
    expect(screen.getByRole("link", { name: "Command center" })).toHaveAttribute(
      "href",
      "/command-center",
    );
    expect(screen.getByRole("link", { name: "Organization" })).toHaveAttribute(
      "href",
      "/settings/organization",
    );
    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("href", "/settings/users");
  });

  it("hides the Users destination from non-admins", () => {
    mockUseAuth.mockReturnValue({ user: HR });
    render(<MainNav />);

    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });
});
