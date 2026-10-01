/**
 * Command center e2e — the live wall's cross-surface promises:
 *
 * 1. Live (AC12): a manager watches `/command-center` while the seeded worker
 *    checks in on `/attendance` — the row flips to present (late badge per
 *    shift rules) without a reload, within the Phase-2 ≤2s metric measured
 *    from the check-in response.
 * 2. Reconnect (AC13): the wall's socket is dropped and every reconnect
 *    attempt is held down while the worker checks in; after reconnecting, the
 *    fresh snapshot resyncs the change (delivery is at-most-once — nothing is
 *    queued for a disconnected wall).
 * 3. Denied (AC11): an employee sees no Command center nav entry and a direct
 *    visit is redirected to the dashboard.
 * 4. Cookie-less upgrade (AC4): opening the WS URL from a logged-out context
 *    is rejected and delivers zero frames.
 *
 * Two-user pattern (new to this suite): one `browser.newContext()` per user,
 * real UI login each — independent cookie jars, so the manager's socket and
 * the worker's check-in are genuinely two sessions. Seed 003 provides
 * `manager@workpulse.test` (EMP-DEV02) with the worker reporting to them, so
 * the manager-scoped wall covers exactly the worker. Runs serial
 * (`workers: 1`); the suite shares the one seeded worker, so `make check-e2e`
 * must not run concurrently against the same dev database.
 */

import { expect, test, type Browser, type BrowserContext, type Page } from '@playwright/test';

import {
  API_URL,
  MANAGER_EMAIL,
  WORKER_EMAIL,
  boardWorkdayMismatch,
  closeOpenRecords,
  uiLogin,
} from './auth';

const CHECK_IN_URL = `${API_URL}/api/v1/attendance/check-ins`;
const BOARD_WS_URL = `${API_URL.replace(/^http/, 'ws')}/api/v1/realtime/board`;
const WORKER_NAME = 'Wendy Worker';

/** Fresh context + real UI login — the two-user bridge at the suite's edge. */
async function loggedIn(
  browser: Browser,
  email: string,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await uiLogin(page, email);
  return { context, page };
}

/** The wall's live connection strip, distinct from "Live updates unavailable". */
function liveBanner(page: Page) {
  return page.getByText('Live', { exact: true });
}

function workerRow(page: Page, status: string) {
  return page.getByTestId(`presence-${status}`).filter({ hasText: WORKER_NAME });
}

test.describe.serial('command center', () => {
  test('a check-in reaches the wall in under 2s without a reload', async ({ browser }) => {
    const worker = await loggedIn(browser, WORKER_EMAIL);
    await closeOpenRecords(worker.context);

    const manager = await loggedIn(browser, MANAGER_EMAIL);
    // The wall's board date is the server's UTC date while the check-in is
    // stamped with the HCM-local workday; skip when they differ (see
    // `boardWorkdayMismatch`) instead of failing on wall clock.
    const workdayMismatch = await boardWorkdayMismatch(manager.context);
    test.skip(workdayMismatch !== null, workdayMismatch ?? undefined);
    await manager.page.goto('/command-center');
    await expect(manager.page.getByTestId('presence-table')).toBeVisible();
    // The wall is streaming before the measurement starts.
    await expect(liveBanner(manager.page)).toBeVisible();

    // Check in through the worker's real UI (server time only).
    await worker.page.goto('/attendance');
    await expect(worker.page.getByRole('button', { name: 'Check in' })).toBeVisible();
    const [checkIn] = await Promise.all([
      worker.page.waitForResponse(
        (response) => response.request().method() === 'POST' && response.url() === CHECK_IN_URL,
      ),
      worker.page.getByRole('button', { name: 'Check in' }).click(),
    ]);
    expect(checkIn.status(), await checkIn.text()).toBe(201);
    const record = (await checkIn.json()) as { late: boolean };

    // The delta must land on the open socket without any navigation: measured
    // from the check-in response (the Phase-2 metric).
    const started = Date.now();
    await expect(workerRow(manager.page, 'present')).toBeVisible({ timeout: 2_000 });
    const elapsed = Date.now() - started;
    expect(elapsed, `live delta took ${elapsed}ms (> 2s)`).toBeLessThan(2_000);

    // `late` is judged by the HCM shift's start + grace at check-in time; the
    // wall must carry exactly what the API recorded (minutes recomputed).
    if (record.late) {
      await expect(workerRow(manager.page, 'present').getByText(/^Late/)).toBeVisible();
    } else {
      await expect(workerRow(manager.page, 'present').getByText(/^Late/)).toHaveCount(0);
    }

    // The same socket carries the check-out: the row flips to checked out.
    await worker.page.getByRole('button', { name: 'Check out' }).click();
    await expect(workerRow(manager.page, 'checked_out')).toBeVisible({ timeout: 2_000 });

    await worker.context.close();
    await manager.context.close();
  });

  test('a dropped socket reconnects and resyncs from a fresh snapshot', async ({ browser }) => {
    const worker = await loggedIn(browser, WORKER_EMAIL);
    await closeOpenRecords(worker.context);

    const managerContext = await browser.newContext();
    // Track every socket the wall opens, so the test can drop the live one
    // from page JS (and prove reconnect behavior, not a page reload).
    await managerContext.addInitScript(() => {
      const OriginalWebSocket = window.WebSocket;
      const sockets: WebSocket[] = [];
      class TrackingWebSocket extends OriginalWebSocket {
        constructor(url: string | URL, protocols?: string | string[]) {
          super(url, protocols);
          sockets.push(this);
        }
      }
      window.WebSocket = TrackingWebSocket as unknown as typeof WebSocket;
      (window as unknown as { __wpE2ESockets?: WebSocket[] }).__wpE2ESockets = sockets;
    });
    const managerPage = await managerContext.newPage();
    await uiLogin(managerPage, MANAGER_EMAIL);
    const workdayMismatch = await boardWorkdayMismatch(managerContext);
    test.skip(workdayMismatch !== null, workdayMismatch ?? undefined);
    await managerPage.goto('/command-center');
    await expect(managerPage.getByTestId('presence-table')).toBeVisible();
    await expect(liveBanner(managerPage)).toBeVisible();

    // Hold every reconnect attempt down (the context goes offline), then drop
    // the live socket from page JS: the wall degrades in place — no reload,
    // snapshot stays.
    await managerContext.setOffline(true);
    await managerPage.evaluate(() => {
      const sockets = (window as unknown as { __wpE2ESockets?: WebSocket[] }).__wpE2ESockets;
      for (const socket of sockets ?? []) {
        socket.close();
      }
    });
    await expect(managerPage.getByText('Live updates unavailable')).toBeVisible();
    await expect(managerPage.getByTestId('presence-table')).toBeVisible();

    // A change made while the wall is disconnected: delivery is at-most-once,
    // so nothing is queued for it — only a reconnect's snapshot can show it.
    await worker.page.goto('/attendance');
    await expect(worker.page.getByRole('button', { name: 'Check in' })).toBeVisible();
    await worker.page.getByRole('button', { name: 'Check in' }).click();
    await expect(worker.page.getByTestId('open-record')).toBeVisible();
    await expect(workerRow(managerPage, 'present')).toHaveCount(0);

    // Let the next retry through: it reconnects, the first frame is a fresh
    // snapshot, and the missed check-in appears.
    await managerContext.setOffline(false);
    await expect(workerRow(managerPage, 'present')).toBeVisible({ timeout: 20_000 });
    await expect(liveBanner(managerPage)).toBeVisible();

    await closeOpenRecords(worker.context);
    await worker.context.close();
    await managerContext.close();
  });

  test('an employee neither sees nor reaches the command center', async ({ browser }) => {
    const worker = await loggedIn(browser, WORKER_EMAIL);

    // Hidden from the navigation…
    await expect(worker.page.getByRole('link', { name: 'Command center' })).toHaveCount(0);

    // …and unreachable: the direct visit is redirected to the dashboard, and
    // no board ever renders.
    await worker.page.goto('/command-center');
    await expect(worker.page).toHaveURL(/\/dashboard$/);
    await expect(worker.page.getByTestId('presence-table')).toHaveCount(0);

    await worker.context.close();
  });

  test('a cookie-less WebSocket upgrade is rejected with zero frames', async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto('/login');

    const outcome = await page.evaluate(
      (url) =>
        new Promise<{ opened: boolean; frames: number; closed: boolean }>((resolve) => {
          const socket = new WebSocket(url);
          let opened = false;
          let frames = 0;
          let closed = false;
          const finish = () => resolve({ opened, frames, closed });
          socket.onopen = () => {
            opened = true;
          };
          socket.onmessage = () => {
            frames += 1;
          };
          socket.onclose = () => {
            closed = true;
            // Give a rejected frame a beat to arrive before settling.
            setTimeout(finish, 250);
          };
          setTimeout(finish, 5_000);
        }),
      BOARD_WS_URL,
    );

    // The handshake is denied before any snapshot frame (close-before-accept).
    expect(outcome.opened).toBe(false);
    expect(outcome.closed).toBe(true);
    expect(outcome.frames).toBe(0);

    await context.close();
  });
});
