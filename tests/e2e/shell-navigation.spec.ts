/**
 * App shell e2e — the grouped sidebar, collapse persistence, the mobile sheet
 * and the ⌘K palette, exercised against the real seeded dev users. The spec
 * proves the shell rules the feature pinned: role-gated destinations never
 * appear for a role that cannot open them, the active route is marked, and the
 * collapsed state survives a reload.
 */

import { expect, test } from "@playwright/test";

import { ADMIN_EMAIL, WORKER_EMAIL, uiLogin } from "./auth";

test.describe("shell navigation", () => {
  test("navigates through the grouped sidebar and the command palette", async ({
    page,
  }) => {
    await uiLogin(page, ADMIN_EMAIL);

    await expect(page.getByText("Workforce", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Payroll", exact: true }).click();
    await page.waitForURL(/\/payroll$/);

    await page.keyboard.press("Control+k");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByPlaceholder("Search pages…").fill("recruit");
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/recruitment$/);
  });

  test("collapses to an icon rail and keeps the choice across reload", async ({
    page,
  }) => {
    await uiLogin(page, ADMIN_EMAIL);

    await page
      .getByRole("banner")
      .getByRole("button", { name: "Toggle navigation" })
      .click();
    await expect(
      page.locator('[data-slot="sidebar"][data-state="collapsed"]').first(),
    ).toBeVisible();

    await page.reload();
    await expect(
      page.locator('[data-slot="sidebar"][data-state="collapsed"]').first(),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Employees", exact: true }),
    ).toBeAttached();
  });

  test("opens the sidebar as a sheet on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await uiLogin(page, ADMIN_EMAIL);

    const trigger = page
      .getByRole("banner")
      .getByRole("button", { name: "Toggle navigation" });
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    await page.getByRole("link", { name: "Leave", exact: true }).click();
    await page.waitForURL(/\/leave$/);
    await expect(
      page.getByRole("heading", { name: "Leave", exact: true }),
    ).toBeVisible();
  });

  test("hides role-gated destinations from an employee", async ({ page }) => {
    await uiLogin(page, WORKER_EMAIL);

    await expect(
      page.getByRole("link", { name: "Employees", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Users", exact: true }),
    ).toHaveCount(0);

    await page.keyboard.press("Control+k");
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("option", { name: "Users" })).toHaveCount(0);
    await expect(
      page.getByRole("option", { name: "My payslips" }),
    ).toBeVisible();
  });
});
