/**
 * Employees e2e — the cross-surface flow this feature promised (AC12):
 * create via the UI → list → detail → transfer → the new period in history,
 * plus a duplicate-email create that must render the 409 as a visible error.
 *
 * Runs against the seeded dev database (`make check-e2e` brings PostgreSQL
 * up, migrates and seeds first); the organizations it reads are seed data
 * (`database/postgres/seeds/001_organizations.sql`). Employment records are
 * append-only and never hard-deleted, so every run namespaces its synthetic
 * employee with a per-run suffix to keep the suite repeatable against the
 * dev database (`.ai/testing.md` rule 6 — synthetic people only).
 */

import { expect, test, type Locator, type Page } from '@playwright/test';

import { ADMIN_EMAIL, uiLogin } from './auth';

const RUN = Date.now().toString(36);
const FIRST_NAME = 'Avery';
const LAST_NAME = `Stone-${RUN}`;
const EMAIL = `e2e.avery.${RUN}@workpulse.test`;

// Fixed past dates: transfer requires `>= hired_at` and `<= today`.
const HIRE_DATE = '2026-08-01';
const TRANSFER_DATE = '2026-08-15';

// Seed organizations.
const FROM_DEPARTMENT = 'Engineering';
const TO_DEPARTMENT = 'People Ops';
const POSITION = 'Software Engineer';
const LOCATION = 'Ho Chi Minh Office';

/** Radix selects render their options in a body portal once the trigger opens. */
async function selectOption(page: Page, scope: Locator, label: string, option: string) {
  await scope.getByLabel(label).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

async function fillCreateForm(page: Page, dialog: Locator, lastName: string) {
  await dialog.getByLabel('First name').fill(FIRST_NAME);
  await dialog.getByLabel('Last name').fill(lastName);
  await dialog.getByLabel('Email').fill(EMAIL);
  await selectOption(page, dialog, 'Department', FROM_DEPARTMENT);
  await selectOption(page, dialog, 'Position', POSITION);
  await selectOption(page, dialog, 'Location', LOCATION);
  await dialog.getByLabel('Hire date').fill(HIRE_DATE);
}

test.describe.serial('employees', () => {
  test.beforeEach(async ({ page }) => {
    // Real login through the UI (Phase 2 authN, decision 5's end state).
    await uiLogin(page, ADMIN_EMAIL);
  });

  test('create → list → detail → transfer leaves the history intact', async ({ page }) => {
    await page.goto('/employees');
    await expect(page.getByRole('heading', { name: 'Employees' })).toBeVisible();

    // Create (UI form → API → DB).
    await page.getByRole('button', { name: 'New employee' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'New employee' })).toBeVisible();
    await fillCreateForm(page, dialog, LAST_NAME);
    await dialog.getByRole('button', { name: 'Create employee' }).click();
    await expect(dialog).toBeHidden();

    // The fresh record is listed and searchable.
    await page.getByLabel('Search employees').fill(EMAIL);
    const row = page.getByRole('row').filter({ hasText: EMAIL });
    await expect(row).toContainText(`${FIRST_NAME} ${LAST_NAME}`);
    await expect(row).toContainText('Active');

    // Detail: profile + the single open employment period.
    await row.getByRole('link', { name: `${FIRST_NAME} ${LAST_NAME}` }).click();
    await expect(page).toHaveURL(/\/employees\/[0-9a-f-]{36}$/);
    await expect(page.getByRole('heading', { name: `${FIRST_NAME} ${LAST_NAME}` })).toBeVisible();

    const periods = page.getByTestId('employment-period');
    await expect(periods).toHaveCount(1);
    await expect(periods.first()).toContainText(FROM_DEPARTMENT);
    await expect(periods.first()).toContainText('Present');
    await expect(periods.first()).toContainText('Current');

    // Transfer: closes the open period and appends a new one (append-only).
    await page.getByRole('button', { name: 'Transfer employee' }).click();
    const transferDialog = page.getByRole('dialog');
    await expect(transferDialog.getByRole('heading', { name: 'Transfer employee' })).toBeVisible();
    await selectOption(page, transferDialog, 'Department', TO_DEPARTMENT);
    await transferDialog.getByLabel('Effective from').fill(TRANSFER_DATE);
    await transferDialog.getByRole('button', { name: 'Record transfer' }).click();
    await expect(transferDialog).toBeHidden();

    // History holds the closed period plus the new open one, newest first.
    await expect(periods).toHaveCount(2);
    await expect(periods.first()).toContainText(TO_DEPARTMENT);
    await expect(periods.first()).toContainText('Present');
    await expect(periods.first()).toContainText('Current');
    await expect(periods.nth(1)).toContainText(FROM_DEPARTMENT);
    await expect(periods.nth(1)).not.toContainText('Current');
  });

  test('duplicate email renders the 409 as a visible field error', async ({ page }) => {
    await page.goto('/employees');
    await page.getByRole('button', { name: 'New employee' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'New employee' })).toBeVisible();
    await fillCreateForm(page, dialog, `Duplicate-${RUN}`);
    await dialog.getByRole('button', { name: 'Create employee' }).click();

    // The dialog stays open and the conflict is visible, not swallowed (AC12).
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('An employee with this email already exists.')).toBeVisible();
  });
});
