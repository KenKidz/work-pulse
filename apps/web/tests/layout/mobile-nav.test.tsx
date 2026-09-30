import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MainNav } from "@/components/main-nav";
import { MobileNav } from "@/components/mobile-nav";

vi.mock("next/navigation", () => ({
  usePathname: () => "/attendance",
}));

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
    expect(screen.getByRole("link", { name: "Organization" })).toHaveAttribute(
      "href",
      "/settings/organization",
    );
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
  it("preserves the desktop navigation destinations", () => {
    render(<MainNav />);

    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: "Employees" })).toHaveAttribute("href", "/employees");
    expect(screen.getByRole("link", { name: "Attendance" })).toHaveAttribute("href", "/attendance");
    expect(screen.getByRole("link", { name: "Organization" })).toHaveAttribute(
      "href",
      "/settings/organization",
    );
  });
});
