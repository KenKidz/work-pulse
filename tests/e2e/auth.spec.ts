/**
 * Auth e2e (spec AC11/AC13/AC14) — the login surface end to end:
 * unauthenticated visits to protected routes redirect to `/login` (cookie
 * guard), bad credentials keep the user on the page with the visible
 * `invalid_credentials` state, a seeded user logs in through the real UI and
 * lands on the dashboard, signing out ends the session (the cookie is gone),
 * and a request carrying only the legacy dev-identity headers — no session —
 * is rejected by the API (`401 unauthenticated`, fail closed).
 *
 * Runs against the seeded dev database (`make check-e2e` brings PostgreSQL
 * up, migrates and seeds first).
 */

import { expect, test } from '@playwright/test';

import { API_URL, ADMIN_EMAIL, uiLogin } from './auth';

// Seed 001's dev tenant and the seeded admin user id — the legacy headers
// must not resurrect any access even with plausible values.
const DEV_TENANT = '00000000-0000-4000-8000-000000000001';
const DEV_ADMIN = '00000000-0000-4000-8000-000000000011';

test.describe.serial('auth', () => {
  test('unauthenticated visits to protected routes redirect to login', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('bad credentials keep the user on the login page with the error state', async ({
    page,
  }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(ADMIN_EMAIL);
    await page.getByLabel('Password').fill('not-the-password');
    await page.getByRole('button', { name: 'Sign in' }).click();

    // Two `role="alert"` elements exist (the form's state + Next's route
    // announcer) — match the one carrying the message.
    const alert = page.getByRole('alert').filter({ hasText: 'Email or password is incorrect.' });
    await expect(alert).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  });

  test('a seeded user signs in through the UI and lands on the dashboard', async ({ page }) => {
    await uiLogin(page, ADMIN_EMAIL);

    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
    // The header identity shows the signed-in account.
    await expect(page.getByText(ADMIN_EMAIL)).toBeVisible();
  });

  test('signing out ends the session', async ({ page }) => {
    await uiLogin(page, ADMIN_EMAIL);
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/login$/);

    // The cookie is gone: a protected route redirects to login again.
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('a stale session cookie self-heals to the login page instead of looping', async ({
    context,
    page,
  }) => {
    // A cookie whose server-side session is gone (TTL expiry, password
    // reset, deactivation) must not loop /login → /dashboard → 401 forever:
    // the AuthGate signs the stale cookie out first, then lands on the form.
    await context.addCookies([
      { name: 'wp_session', value: 'stale-but-present', domain: 'localhost', path: '/' },
    ]);
    await page.goto('/dashboard');

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByLabel('Email')).toBeVisible();
  });

  test('a request carrying only the legacy dev-identity headers is rejected', async ({
    request,
  }) => {
    const response = await request.get(`${API_URL}/api/v1/employees`, {
      headers: {
        'X-Tenant-Id': DEV_TENANT,
        'X-Actor-Id': DEV_ADMIN,
        'X-Role': 'admin',
      },
    });

    expect(response.status()).toBe(401);
    expect((await response.json()).error?.code).toBe('unauthenticated');
  });
});
