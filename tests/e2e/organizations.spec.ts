/**
 * Organizations e2e — the cross-surface flow this feature promised (AC9):
 * create a department and a position through the real UI → assign both to a
 * new employee → archiving the in-use department must render the Risk-1
 * `409 department_archived_in_use` as a visible error state and keep the row
 * Active; once the employee transfers out, the archive/restore round-trip
 * completes (AC5 across surfaces).
 *
 * Runs against the seeded dev database (`make check-e2e` brings PostgreSQL up,
 * migrates and seeds first); the transfer target and location are seed data
 * (`database/postgres/seeds/001_organizations.sql`). Department names are
 * unique per tenant across ALL rows (archived included), and employees are
 * append-only, so every run namespaces its department, position and employee
 * with a per-run suffix to keep the suite repeatable against the dev database
 * (`.ai/testing.md` rule 6 — synthetic people only).
 */

import { expect, test, type Locator, type Page } from '@playwright/test';

import { ADMIN_EMAIL, login } from './auth';

const RUN = Date.now().toString(36);
const DEPARTMENT = `Field Ops-${RUN}`;
const POSITION_TITLE = `Ops Lead-${RUN}`;
const FIRST_NAME = 'Jordan';
const LAST_NAME = `Reed-${RUN}`;
const EMAIL = `e2e.jordan.${RUN}@workpulse.test`;

// Seed organization/location the employee is created and transferred against.
const SEED_DEPARTMENT = 'People Ops';
const SEED_LOCATION = 'Ho Chi Minh Office';

// Fixed past dates: transfer requires `>= hired_at` and `<= today`.
const HIRE_DATE = '2026-08-01';
const TRANSFER_DATE = '2026-09-01';

/** Radix selects render their options in a body portal once the trigger opens. */
async function selectOption(page: Page, scope: Locator, label: string, option: string) {
  await scope.getByLabel(label).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

/** The table row for a department/position, matched by its unique run name. */
function namedRow(page: Page, name: string): Locator {
  return page.getByRole('row').filter({ hasText: name });
}

test.describe.serial('organizations', () => {
  test.beforeEach(async ({ context }) => {
    // Real login (Phase 2 authN): the seeded admin's cookie drives the page.
    await login(context, ADMIN_EMAIL);
  });

  test('create a department and a position; both list as Active', async ({ page }) => {
    await page.goto('/settings/organization/departments');
    await expect(page.getByRole('heading', { name: 'Organization' })).toBeVisible();

    // Department (UI form → API → DB).
    await page.getByRole('button', { name: 'New department' }).click();
    const deptDialog = page.getByRole('dialog');
    await expect(deptDialog.getByRole('heading', { name: 'New department' })).toBeVisible();
    await deptDialog.getByLabel('Name').fill(DEPARTMENT);
    await deptDialog.getByRole('button', { name: 'Create department' }).click();
    await expect(deptDialog).toBeHidden();

    const deptRow = namedRow(page, DEPARTMENT);
    await expect(deptRow).toContainText('Active');

    // Position — same flow on the positions tab.
    await page.getByRole('link', { name: 'Positions' }).click();
    await page.getByRole('button', { name: 'New position' }).click();
    const positionDialog = page.getByRole('dialog');
    await expect(positionDialog.getByRole('heading', { name: 'New position' })).toBeVisible();
    await positionDialog.getByLabel('Title').fill(POSITION_TITLE);
    await positionDialog.getByRole('button', { name: 'Create position' }).click();
    await expect(positionDialog).toBeHidden();

    await expect(namedRow(page, POSITION_TITLE)).toContainText('Active');
  });

  test('assigning both to an employee blocks the archive with the visible 409 (Risk 1)', async ({
    page,
  }) => {
    // The fresh org data is immediately assignable on the employee form.
    await page.goto('/employees');
    await page.getByRole('button', { name: 'New employee' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'New employee' })).toBeVisible();
    await dialog.getByLabel('First name').fill(FIRST_NAME);
    await dialog.getByLabel('Last name').fill(LAST_NAME);
    await dialog.getByLabel('Email').fill(EMAIL);
    await selectOption(page, dialog, 'Department', DEPARTMENT);
    await selectOption(page, dialog, 'Position', POSITION_TITLE);
    await selectOption(page, dialog, 'Location', SEED_LOCATION);
    await dialog.getByLabel('Hire date').fill(HIRE_DATE);
    await dialog.getByRole('button', { name: 'Create employee' }).click();
    await expect(dialog).toBeHidden();

    await page.getByLabel('Search employees').fill(EMAIL);
    await expect(page.getByRole('row').filter({ hasText: EMAIL })).toContainText('Active');

    // The department is now in use: the archive fails with the pinned 409
    // rendered as a visible error state, and the row stays Active (AC5/AC9).
    await page.goto('/settings/organization/departments');
    await page.getByRole('button', { name: `Archive ${DEPARTMENT}`, exact: true }).click();
    await expect(page.getByText('Department still has active employees')).toBeVisible();
    await expect(namedRow(page, DEPARTMENT)).toContainText('Active');
  });

  test('once the employee transfers out, archive and restore round-trip', async ({ page }) => {
    // Move the employee to the seeded department (append-only history).
    await page.goto('/employees');
    await page.getByLabel('Search employees').fill(EMAIL);
    await page
      .getByRole('row')
      .filter({ hasText: EMAIL })
      .getByRole('link', { name: `${FIRST_NAME} ${LAST_NAME}` })
      .click();
    await expect(page).toHaveURL(/\/employees\/[0-9a-f-]{36}$/);

    await page.getByRole('button', { name: 'Transfer employee' }).click();
    const transferDialog = page.getByRole('dialog');
    await expect(
      transferDialog.getByRole('heading', { name: 'Transfer employee' }),
    ).toBeVisible();
    await selectOption(page, transferDialog, 'Department', SEED_DEPARTMENT);
    await transferDialog.getByLabel('Effective from').fill(TRANSFER_DATE);
    await transferDialog.getByRole('button', { name: 'Record transfer' }).click();
    await expect(transferDialog).toBeHidden();

    // Nobody holds the department any more — the archive succeeds now.
    await page.goto('/settings/organization/departments');
    await page.getByRole('button', { name: `Archive ${DEPARTMENT}`, exact: true }).click();
    await expect(page.getByText('Department archived')).toBeVisible();
    await expect(namedRow(page, DEPARTMENT)).toBeHidden();

    // Archived rows hide from the default list and come back on the toggle.
    await page.getByRole('button', { name: 'Include archived' }).click();
    await expect(namedRow(page, DEPARTMENT)).toContainText('Archived');

    // Restore is the trivial inverse (Decision #5).
    await page.getByRole('button', { name: `Restore ${DEPARTMENT}`, exact: true }).click();
    await expect(page.getByText('Department restored')).toBeVisible();
    await expect(namedRow(page, DEPARTMENT)).toContainText('Active');
  });
});
