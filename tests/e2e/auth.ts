/**
 * Real-login bridge and shared API helpers for the e2e suite (Phase 2 authN —
 * ADR 0004's swap).
 *
 * `make check-e2e` seeds the dev users (`database/postgres/seeds/003_users.sql`
 * — synthetic, dev-only). Every suite logs in through the real UI
 * (decision 5's end state): the form POST goes through the api-client, the
 * API sets the `wp_session` cookie in the browser context's cookie jar, and
 * the page's API calls carry it automatically (the transport sends
 * `credentials: "include"`). No dev-identity headers exist anymore — a
 * request with only the legacy headers is rejected (`auth.spec.ts` proves it).
 */

import { expect, type BrowserContext, type Page } from '@playwright/test';

export const API_URL = "http://localhost:8000";

/** Seed 003's shared dev password (synthetic, dev-only). */
export const SEEDED_PASSWORD = "workpulse-dev";

export const ADMIN_EMAIL = "admin@workpulse.test";
export const HR_EMAIL = "hr@workpulse.test";
/** Linked to the synthetic employee `00000000-0000-4000-8000-000000000021`. */
export const WORKER_EMAIL = "worker@workpulse.test";
export const WORKER_EMPLOYEE_ID = "00000000-0000-4000-8000-000000000021";
/** Linked to `00000000-0000-4000-8000-000000000023`; the worker reports to them. */
export const MANAGER_EMAIL = "manager@workpulse.test";

/** Log a seeded user in through the real UI; lands on the dashboard. */
export async function uiLogin(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  // `exact: true` — the login page also has a "Show password" toggle whose
  // accessible name substring-matches "Password".
  await page.getByLabel("Password", { exact: true }).fill(SEEDED_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/dashboard$/);
}

/**
 * Close any record the worker left open — regardless of workday. The UI's
 * open-record banner is date-scoped, but the API rejects a duplicate check-in
 * for any open record, so recovery goes through the API: an interrupted earlier
 * run may have crossed midnight in the employee's timezone. Shared by the
 * attendance and command-center suites, which both mutate the one seeded
 * worker (`.ai/testing.md` rule 6 — synthetic data only).
 */
export async function closeOpenRecords(context: BrowserContext): Promise<void> {
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

/**
 * Reason to skip the command-center live flows, or `null` when they can run.
 *
 * The wall's board date is the server's **UTC** date (pinned contract), while
 * a check-in is stamped with the employee's location-local workday (ADR 0009).
 * For the seeded HCM worker (UTC+7) the two differ between 00:00 and 07:00
 * local: the fresh record is for "tomorrow" relative to the wall's snapshot
 * date, so the live row can never appear and the test would fail on wall
 * clock. Callers `test.skip()` with this reason instead.
 */
export async function boardWorkdayMismatch(
  context: BrowserContext,
): Promise<string | null> {
  const response = await context.request.get(`${API_URL}/api/v1/realtime/board`);
  expect(response.status(), await response.text()).toBe(200);
  const { date } = (await response.json()) as { date: string };
  const workday = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  if (date === workday) return null;
  return `wall is on ${date} but the worker's HCM workday is ${workday} — the live check-in cannot appear on the server-UTC board`;
}
