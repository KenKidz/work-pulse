/**
 * Attendance e2e — the cross-surface flow the feature promised (AC12):
 * check in through the real UI → the board counts and activity feed update →
 * check out → the record closes and `checked_out` moves; a second check-in
 * attempt while a record is open must render the pinned 409 as a visible error
 * state.
 *
 * Runs against the seeded dev database (`make check-e2e` brings PostgreSQL up,
 * migrates and seeds first): the employee is created via the API against the
 * seeded organization refs (fixed UUIDs, `database/postgres/seeds/001`), and
 * lateness is judged by seed 002's Ho Chi Minh Office shift.
 *
 * Phase 1 identity is the dev headers (ADR 0004). The browser's identity is
 * baked into the client bundle (`NEXT_PUBLIC_DEV_*`) when the web server
 * starts, so a per-run employee cannot become the actor through env alone: the
 * fixture creates a fresh employee each run and the spec impersonates it by
 * rewriting the pinned `X-Actor-Id` header on the page's API calls. The
 * check-in/check-out then runs through the real UI exactly as that employee —
 * a fresh actor keeps the suite repeatable against the dev database
 * (`.ai/testing.md` rule 6 — synthetic people only).
 */

import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from '@playwright/test';

const RUN = Date.now().toString(36);
const EMAIL = `e2e.attendance.${RUN}@workpulse.test`;

// Dev identity + seed organizations (database/postgres/seeds/001, 002).
const TENANT_ID = '00000000-0000-4000-8000-000000000001';
const ADMIN_ACTOR_ID = '00000000-0000-4000-8000-000000000002';
const DEPARTMENT_ENGINEERING = '00000000-0000-4000-8000-0000000d0002';
const POSITION_ENGINEER = '00000000-0000-4000-8000-0000000e0001';
const LOCATION_HCM = '00000000-0000-4000-8000-0000000c0001';

const API_URL = 'http://localhost:8000';

const ADMIN_HEADERS = {
  'X-Tenant-Id': TENANT_ID,
  'X-Actor-Id': ADMIN_ACTOR_ID,
  'X-Role': 'admin',
};

/** Create a synthetic employee via the API; returns its id. */
async function createEmployee(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${API_URL}/api/v1/employees`, {
    headers: { ...ADMIN_HEADERS, 'Content-Type': 'application/json' },
    data: {
      first_name: 'Ada',
      last_name: `Attendance-${RUN}`,
      email: EMAIL,
      department_id: DEPARTMENT_ENGINEERING,
      position_id: POSITION_ENGINEER,
      location_id: LOCATION_HCM,
      employment_type: 'full_time',
      hired_at: '2026-08-01',
    },
  });
  expect(response.status(), await response.text()).toBe(201);
  return ((await response.json()) as { id: string }).id;
}

/**
 * Make every API call the page makes act as `actorId` — the Phase 1 dev
 * identity header is pinned in the bundle, so e2e rewrites it per request.
 */
async function impersonate(context: BrowserContext, actorId: string): Promise<void> {
  await context.route(`${API_URL}/**`, async (route) => {
    const headers = route.request().headers();
    if (headers['x-actor-id'] === undefined) {
      // Preflights and anything without identity pass through untouched.
      await route.continue();
      return;
    }
    await route.continue({ headers: { ...headers, 'x-actor-id': actorId } });
  });
}

/** Today in the employee's location timezone — the API's workday definition. */
function workdayISO(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Ho_Chi_Minh',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());
}

/** The board/feed/table render employee ids as `Employee …{last four}`. */
function shortId(id: string): string {
  return `Employee …${id.slice(-4)}`;
}

function count(page: Page, testId: string): Promise<number> {
  return page.getByTestId(testId).innerText().then(Number);
}

test.describe.serial('attendance', () => {
  test('check in → board updates → check out; a duplicate check-in shows the 409', async ({
    context,
    page,
    request,
  }) => {
    const employeeId = await createEmployee(request);
    await impersonate(context, employeeId);

    const workday = workdayISO();

    // Tab A — the employee's own panel on the explicit workday date.
    await page.goto('/attendance');
    await expect(page.getByRole('heading', { name: 'Attendance', exact: true })).toBeVisible();
    await page.getByLabel("Date", { exact: true }).fill(workday);
    await expect(page.getByTestId('check-in-panel')).toBeVisible();

    // Tab B opens before the check-in: its cache never sees the open record,
    // so its later submit is the concurrent two-tab duplicate (spec edge case).
    const staleTab = await context.newPage();
    await staleTab.goto('/attendance');
    await staleTab.getByLabel("Date", { exact: true }).fill(workday);
    await expect(staleTab.getByRole('button', { name: 'Check in' })).toBeVisible();

    const presentBefore = await count(page, 'count-present');
    const checkedOutBefore = await count(page, 'count-checked-out');

    // Check in through the UI — server time only (Decision #3).
    await page.getByRole('button', { name: 'Check in' }).click();
    await expect(page.getByTestId('open-record')).toBeVisible();
    await expect(page.getByTestId('count-present')).toHaveText(String(presentBefore + 1));

    const feed = page.getByTestId('activity-feed');
    await expect(
      feed.locator('li').filter({ hasText: `${shortId(employeeId)} checked in` }).first(),
    ).toBeVisible();

    // The stale tab submits its form: the API rejects the second check-in and
    // the panel renders the pinned visible error state (AC12).
    await staleTab.getByRole('button', { name: 'Check in' }).click();
    await expect(
      staleTab.getByTestId('check-in-panel').getByRole('alert'),
    ).toContainText('already checked in');

    // The open record is listed while it is open…
    const recordRow = page
      .getByTestId('attendance-records')
      .getByRole('row')
      .filter({ hasText: shortId(employeeId) });
    await expect(recordRow.first()).toContainText('Open');

    // …and check-out closes it; the board reflects the moved counts.
    await page.getByRole('button', { name: 'Check out' }).click();
    await expect(page.getByTestId('open-record')).toBeHidden();
    await expect(page.getByTestId('count-present')).toHaveText(String(presentBefore));
    await expect(page.getByTestId('count-checked-out')).toHaveText(String(checkedOutBefore + 1));
    await expect(
      feed.locator('li').filter({ hasText: `${shortId(employeeId)} checked out` }).first(),
    ).toBeVisible();
    await expect(recordRow.first()).not.toContainText('Open');

    await staleTab.close();
  });
});
