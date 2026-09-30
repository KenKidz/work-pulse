/**
 * Attendance e2e — the cross-surface flow the feature promised (AC12):
 * check in through the real UI → the open record and its row appear → check
 * out closes it; a second check-in attempt while a record is open must render
 * the pinned 409 as a visible error state.
 *
 * Phase 2 authN (ADR 0004's swap): the browser acts as the seeded worker user
 * (`worker@workpulse.test`, linked to synthetic employee `...0021` by seed
 * 003), logged in for real through the API — the dev-identity header
 * impersonation is deleted, not flagged. An employee-scoped check-in needs an
 * employee link, and only the seeded worker has one (the admin and HR seeds
 * are unlinked accounts; user provisioning arrives in T6).
 *
 * Runs against the seeded dev database (`make check-e2e` brings PostgreSQL
 * up, migrates and seeds first). Repeatable: the worker's record is closed at
 * the end, and any record left open by an interrupted earlier run — on any
 * workday — is closed through the API up front, because the duplicate-check-in
 * guard is workday-agnostic while the panel banner is date-scoped
 * (`.ai/testing.md` rule 6 — synthetic people only).
 *
 * The suite shares the one seeded worker, so `make check-e2e` must not run
 * concurrently against the same dev database: a second runner would race this
 * spec on the one-open-record invariant (Playwright's `workers: 1` serializes
 * within a single run only).
 */

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { API_URL, WORKER_EMAIL, WORKER_EMPLOYEE_ID, login } from './auth';

/** Today in the employee's location timezone — the API's workday definition. */
function workdayISO(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Ho_Chi_Minh',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

/** The records table renders employee ids as `Employee …{last four}`. */
function shortId(id: string): string {
  return `Employee …${id.slice(-4)}`;
}

async function openAttendance(page: Page, workday: string): Promise<void> {
  await page.goto('/attendance');
  await expect(page.getByRole('heading', { name: 'Attendance', exact: true })).toBeVisible();
  await page.getByLabel('Date', { exact: true }).fill(workday);
  await expect(page.getByTestId('check-in-panel')).toBeVisible();
}

/**
 * Close any record the worker left open — regardless of workday. The UI's
 * open-record banner is date-scoped, but the API rejects a duplicate check-in
 * for any open record, so recovery goes through the API: an interrupted earlier
 * run may have crossed midnight in the employee's timezone.
 */
async function closeOpenRecords(context: BrowserContext): Promise<void> {
  const response = await context.request.get(
    `${API_URL}/api/v1/attendance/records?page_size=100`,
  );
  expect(response.status(), await response.text()).toBe(200);
  const { items } = (await response.json()) as {
    items: { id: string; checked_out_at: string | null }[];
  };
  for (const record of items.filter((item) => item.checked_out_at === null)) {
    const closed = await context.request.post(
      `${API_URL}/api/v1/attendance/records/${record.id}/check-out`,
      { data: {} },
    );
    expect(closed.status(), await closed.text()).toBe(200);
  }
}

test.describe.serial('attendance', () => {
  test('check in → record opens → check out; a duplicate check-in shows the 409', async ({
    context,
    page,
  }) => {
    await login(context, WORKER_EMAIL);
    // An interrupted earlier run may have left the worker's day open on any
    // workday — recover through the API before the UI flow starts.
    await closeOpenRecords(context);

    const workday = workdayISO();
    await openAttendance(page, workday);

    const openRecord = page.getByTestId('open-record');
    await expect(page.getByRole('button', { name: 'Check in' })).toBeVisible();

    // Tab B opens before the check-in: its cache never sees the open record,
    // so its later submit is the concurrent two-tab duplicate (spec edge case).
    const staleTab = await context.newPage();
    await openAttendance(staleTab, workday);
    await expect(staleTab.getByRole('button', { name: 'Check in' })).toBeVisible();

    // Check in through the UI — server time only (Decision #3).
    await page.getByRole('button', { name: 'Check in' }).click();
    await expect(openRecord).toBeVisible();

    const recordRow = page
      .getByTestId('attendance-records')
      .getByRole('row')
      .filter({ hasText: shortId(WORKER_EMPLOYEE_ID) });
    await expect(recordRow.first()).toContainText('Open');

    // The stale tab submits its form: the API rejects the second check-in and
    // the panel renders the pinned visible error state (AC12).
    await staleTab.getByRole('button', { name: 'Check in' }).click();
    await expect(staleTab.getByTestId('check-in-panel').getByRole('alert')).toContainText(
      'already checked in',
    );

    // Check-out closes the record and the row follows the moved state.
    await page.getByRole('button', { name: 'Check out' }).click();
    await expect(openRecord).toBeHidden();
    await expect(recordRow.first()).not.toContainText('Open');

    await staleTab.close();
  });
});
