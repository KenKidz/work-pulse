/**
 * Real-login bridge for the e2e suite (Phase 2 authN — ADR 0004's swap).
 *
 * `make check-e2e` seeds the dev users (`database/postgres/seeds/003_users.sql`
 * — synthetic, dev-only). Every suite logs in through the real UI
 * (decision 5's end state): the form POST goes through the api-client, the
 * API sets the `wp_session` cookie in the browser context's cookie jar, and
 * the page's API calls carry it automatically (the transport sends
 * `credentials: "include"`). No dev-identity headers exist anymore — a
 * request with only the legacy headers is rejected (`auth.spec.ts` proves it).
 */

import { expect, type Page } from '@playwright/test';

export const API_URL = "http://localhost:8000";

/** Seed 003's shared dev password (synthetic, dev-only). */
export const SEEDED_PASSWORD = "workpulse-dev";

export const ADMIN_EMAIL = "admin@workpulse.test";
export const HR_EMAIL = "hr@workpulse.test";
/** Linked to the synthetic employee `00000000-0000-4000-8000-000000000021`. */
export const WORKER_EMAIL = "worker@workpulse.test";
export const WORKER_EMPLOYEE_ID = "00000000-0000-4000-8000-000000000021";

/** Log a seeded user in through the real UI; lands on the dashboard. */
export async function uiLogin(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(SEEDED_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/dashboard$/);
}
