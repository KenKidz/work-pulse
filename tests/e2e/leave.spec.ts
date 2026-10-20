/**
 * Leave e2e — the cross-surface promise of the feature (T9):
 *
 * 1. The seeded worker submits an unpaid request through `/leave`; it lands in
 *    "My requests" as PENDING with the inclusive day count.
 * 2. The same form refuses a second, overlapping range with the pinned
 *    `409 leave_overlaps` rendered inline (the suite's failure case).
 * 3. The manager approves it from the team inbox while a second page watches
 *    `/command-center`; the approval travels outbox → relay → realtime
 *    consumer → Redis and the wall flips the worker to `on_leave`, removes her
 *    from `absent` and bumps `count-on-leave` — without a reload, within the
 *    Phase-2 ≤2s metric measured from the approve response.
 *
 * The request must cover the wall's date (the server's UTC workday, pinned in
 * the realtime contract) for the consumer's windowed mapping (ADR 0014) to
 * emit a presence fact. Approved leave is terminal and there is no delete API,
 * so `make check-e2e`'s hop runner (`full-hop.sh`) resets the seeded worker's
 * leave fixtures before Playwright starts — re-runs stay green against the
 * same dev database (`.ai/testing.md` rule 6 — synthetic data only).
 */

import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from '@playwright/test';

import {
  API_URL,
  MANAGER_EMAIL,
  WORKER_EMAIL,
  closeOpenRecords,
  uiLogin,
} from './auth';

const WORKER_NAME = 'Wendy Worker';
const LEAVE_REQUESTS_URL = `${API_URL}/api/v1/leave/requests`;
const BOARD_URL = `${API_URL}/api/v1/realtime/board`;

/** Fresh context + real UI login — independent cookie jars for the two users. */
async function loggedIn(
  browser: Browser,
  email: string,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await uiLogin(page, email);
  return { context, page };
}

/** Radix selects render their options in a body portal once the trigger opens. */
async function selectOption(page: Page, scope: Locator, label: string, option: string) {
  await scope.getByLabel(label).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

/** Fill the request form for one unpaid day covering the wall's date. */
async function fillUnpaidRequest(page: Page, dialog: Locator, boardDate: string) {
  await selectOption(page, dialog, 'Leave type', 'Unpaid leave');
  await dialog.getByLabel('Start date').fill(boardDate);
  await dialog.getByLabel('End date').fill(boardDate);
}

async function submitRequest(page: Page, dialog: Locator) {
  return Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response.url() === LEAVE_REQUESTS_URL,
    ),
    dialog.getByRole('button', { name: 'Submit request' }).click(),
  ]);
}

test.describe.serial('leave', () => {
  test('request → approve → the wall shows on leave without a reload', async ({
    browser,
  }) => {
    const worker = await loggedIn(browser, WORKER_EMAIL);
    await closeOpenRecords(worker.context);
    const manager = await loggedIn(browser, MANAGER_EMAIL);

    // The wall boards the server's UTC date; a request covering it is the only
    // shape the consumer's windowed `leave.approved` mapping emits.
    const boardResponse = await manager.context.request.get(BOARD_URL);
    expect(boardResponse.status(), await boardResponse.text()).toBe(200);
    const { date: boardDate } = (await boardResponse.json()) as { date: string };

    // --- Worker: submit through the real UI -------------------------------
    await worker.page.goto('/leave');
    await expect(worker.page.getByRole('heading', { name: 'Leave' })).toBeVisible();
    await worker.page.getByRole('button', { name: 'Request leave' }).click();
    let dialog = worker.page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'Request leave' })).toBeVisible();
    await fillUnpaidRequest(worker.page, dialog, boardDate);

    const [created] = await submitRequest(worker.page, dialog);
    expect(created.status(), await created.text()).toBe(201);
    const request = (await created.json()) as {
      id: string;
      status: string;
      start_date: string;
      end_date: string;
      days: number;
    };
    expect(request.status).toBe('pending');
    expect(request.start_date).toBe(boardDate);
    expect(request.end_date).toBe(boardDate);
    expect(request.days).toBe(1);
    await expect(dialog).toBeHidden();

    // "My requests" reflects the fresh PENDING row without a reload.
    const myRow = worker.page
      .getByRole('row')
      .filter({ hasText: 'Unpaid leave' })
      .filter({ hasText: 'Pending' });
    await expect(myRow).toBeVisible();

    // --- Failure case: the same range overlaps the pending request --------
    await worker.page.getByRole('button', { name: 'Request leave' }).click();
    dialog = worker.page.getByRole('dialog');
    await fillUnpaidRequest(worker.page, dialog, boardDate);
    const [overlap] = await submitRequest(worker.page, dialog);
    expect(overlap.status(), await overlap.text()).toBe(409);
    await expect(
      dialog.getByText('This range overlaps a pending or approved leave request.'),
    ).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // --- Manager: watch the wall, then approve from the team inbox --------
    const wall = await manager.context.newPage();
    await wall.goto('/command-center');
    await expect(wall.getByTestId('presence-table')).toBeVisible();
    await expect(wall.getByText('Live', { exact: true })).toBeVisible();

    const absentBefore = Number(
      await wall.getByTestId('count-absent').innerText(),
    );
    const workerWasAbsent =
      (await wall
        .getByTestId('presence-absent')
        .filter({ hasText: WORKER_NAME })
        .count()) > 0;

    await manager.page.goto('/leave');
    await expect(manager.page.getByRole('heading', { name: 'Leave' })).toBeVisible();
    const approvalRow = manager.page
      .getByRole('row')
      .filter({ hasText: WORKER_NAME })
      .filter({ hasText: 'Unpaid leave' });
    await expect(approvalRow).toBeVisible();
    await approvalRow
      .getByRole('button', { name: `Approve leave request for ${WORKER_NAME}` })
      .click();

    const decision = manager.page.getByRole('dialog');
    await expect(
      decision.getByRole('heading', { name: 'Approve leave request' }),
    ).toBeVisible();
    await decision.getByLabel('Decision note (optional)').fill('Approved via e2e.');

    const started = Date.now();
    const [approved] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === 'POST' &&
          response.url() === `${LEAVE_REQUESTS_URL}/${request.id}/approve`,
      ),
      decision.getByRole('button', { name: 'Approve request' }).click(),
    ]);
    expect(approved.status(), await approved.text()).toBe(200);

    // The delta must land on the open socket without any navigation: measured
    // from the approve response (the Phase-2 metric).
    await expect(
      wall.getByTestId('presence-on_leave').filter({ hasText: WORKER_NAME }),
    ).toBeVisible({ timeout: 2_000 });
    const elapsed = Date.now() - started;
    expect(elapsed, `live leave delta took ${elapsed}ms (> 2s)`).toBeLessThan(2_000);

    await expect(wall.getByTestId('count-on-leave')).toHaveText('1');
    await expect(
      wall.getByTestId('presence-absent').filter({ hasText: WORKER_NAME }),
    ).toHaveCount(0);
    const absentAfter = Number(await wall.getByTestId('count-absent').innerText());
    expect(absentAfter).toBeLessThanOrEqual(absentBefore);
    if (workerWasAbsent) {
      // The worker was in `absent` before approval — leave removes her.
      expect(absentAfter).toBe(absentBefore - 1);
    }

    // The wall page never navigated (the flip came over the socket).
    expect(new URL(wall.url()).pathname).toBe('/command-center');

    await manager.context.close();
    await worker.context.close();
  });
});
