/**
 * Users e2e (spec AC12) — admin user management through the real UI:
 * the admin lists the seeded users, creates a fresh one (unique per run to
 * keep the suite repeatable against the dev database), and deactivates it;
 * a non-admin (seeded HR) can neither see the Users nav entry nor reach the
 * page — the direct visit is redirected to the dashboard.
 *
 * Runs against the seeded dev database (`make check-e2e` brings PostgreSQL
 * up, migrates and seeds first). The created account is deactivated at the
 * end of the flow, so re-runs only need a fresh email (`.ai/testing.md`
 * rule 6 — synthetic data only).
 */

import { expect, test, type Locator, type Page } from '@playwright/test';

import { ADMIN_EMAIL, HR_EMAIL, uiLogin } from './auth';

const RUN = Date.now().toString(36);
const NEW_EMAIL = `e2e.user.${RUN}@workpulse.test`;
const NEW_PASSWORD = 'e2e-password-123';

/** Radix selects render their options in a body portal once the trigger opens. */
async function selectOption(page: Page, scope: Locator, label: string, option: string) {
  await scope.getByLabel(label).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

test.describe.serial('users', () => {
  test('admin creates and deactivates a user through the UI', async ({ page }) => {
    await uiLogin(page, ADMIN_EMAIL);
    await page.goto('/settings/users');
    await expect(page.getByRole('heading', { name: 'Users' })).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: ADMIN_EMAIL })).toBeVisible();

    // Create (UI form → API → DB).
    await page.getByRole('button', { name: 'New user' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'New user' })).toBeVisible();
    await dialog.getByLabel('Email').fill(NEW_EMAIL);
    await dialog.getByLabel('Password').fill(NEW_PASSWORD);
    await selectOption(page, dialog, 'Role', 'Employee');
    await dialog.getByRole('button', { name: 'Create user' }).click();
    await expect(dialog).toBeHidden();

    const row = page.getByRole('row').filter({ hasText: NEW_EMAIL });
    await expect(row).toContainText('Active');

    // Deactivate through the confirm dialog; the row flips to Inactive.
    await row.getByRole('button', { name: `Deactivate ${NEW_EMAIL}` }).click();
    const confirm = page.getByRole('dialog');
    await confirm.getByRole('button', { name: 'Deactivate' }).click();
    await expect(confirm).toBeHidden();
    await expect(row).toContainText('Inactive');
  });

  test('a non-admin cannot see or reach the users page', async ({ page }) => {
    await uiLogin(page, HR_EMAIL);

    // Hidden from the navigation…
    await expect(page.getByRole('link', { name: 'Users' })).toHaveCount(0);

    // …and unreachable: the direct visit is redirected to the dashboard.
    await page.goto('/settings/users');
    await expect(page).toHaveURL(/\/dashboard$/);
  });
});
