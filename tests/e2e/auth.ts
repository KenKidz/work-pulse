/**
 * Real-login bridge for the e2e suite (Phase 2 authN — ADR 0004's swap).
 *
 * `make check-e2e` seeds the dev users (`database/postgres/seeds/003_users.sql`
 * — synthetic, dev-only). Logging in through the API stores the `wp_session`
 * cookie in the browser context's cookie jar, which the page's API calls then
 * carry automatically (the api-client transport sends `credentials: "include"`).
 * The T7 work swaps this helper for the real UI login.
 */

import { expect, type BrowserContext } from '@playwright/test';

export const API_URL = "http://localhost:8000";

/** Seed 003's shared dev password (synthetic, dev-only). */
export const SEEDED_PASSWORD = "workpulse-dev";

export const ADMIN_EMAIL = "admin@workpulse.test";
/** Linked to the synthetic employee `00000000-0000-4000-8000-000000000021`. */
export const WORKER_EMAIL = "worker@workpulse.test";
export const WORKER_EMPLOYEE_ID = "00000000-0000-4000-8000-000000000021";

/** Log a seeded user in for real; the session cookie lands in the context. */
export async function login(context: BrowserContext, email: string): Promise<void> {
  const response = await context.request.post(`${API_URL}/api/v1/auth/login`, {
    data: { email, password: SEEDED_PASSWORD },
  });
  expect(response.status(), await response.text()).toBe(200);
}
