/**
 * Notification inbox e2e — the cross-surface promise of T5/T6:
 *
 * 1. A real login shows the seeded unread count on the header bell.
 * 2. The bell dropdown lists the recent rows and links to `/notifications`.
 * 3. The page lists the stored messages with unread styling; marking one read
 *    drops the badge by one, and "mark all read" takes it to zero with every
 *    row showing its read state — no reloads.
 *
 * Rows are inserted by `full-hop.sh`'s `reset_notification_fixtures()` before
 * Playwright starts. The notification worker stays out of the e2e hop: its
 * delivery contract is already proven at the worker level (spec decision 8).
 */

import { expect, test } from '@playwright/test';

import { WORKER_EMAIL, uiLogin } from './auth';

const FIRST_SUBJECT = 'E2E fixture: leave approved';

test.describe.serial('notification inbox', () => {
  test('badge → dropdown → mark one → mark all read', async ({ page }) => {
    await uiLogin(page, WORKER_EMAIL);

    const badge = page.getByTestId('notification-unread-badge');
    const bell = page.getByTestId('notification-bell');
    await expect(badge).toHaveText('3');
    await expect(bell).toHaveAccessibleName('Notifications, 3 unread');

    // --- The dropdown is a glance at the same person-addressed rows --------
    await bell.click();
    await expect(page.getByTestId('notification-preview')).toHaveCount(3);
    await expect(
      page
        .getByTestId('notification-preview')
        .filter({ hasText: FIRST_SUBJECT }),
    ).toBeVisible();
    await page
      .getByRole('menuitem', { name: 'View all notifications' })
      .click();
    await expect(page).toHaveURL(/\/notifications$/);
    // The dropdown lingers through its close animation; wait it out so the
    // page's unread summary cannot collide with the menu's count badge.
    await expect(page.getByRole('menu')).toHaveCount(0);

    // --- The page: stored message of record, unread styling ---------------
    const rows = page.getByTestId('notification-row');
    await expect(rows).toHaveCount(3);
    await expect(page.getByRole('main').getByText('3 unread')).toBeVisible();
    await expect(rows.filter({ hasText: 'Unread' })).toHaveCount(3);
    await expect(
      rows.filter({ hasText: 'E2E fixture: leave request' }),
    ).toContainText('A leave request is waiting for a decision.');

    // --- Per-item mark-read drops the badge by exactly one ----------------
    const firstRow = rows.filter({ hasText: FIRST_SUBJECT });
    await firstRow.getByRole('button', { name: /^Mark read:/ }).click();
    await expect(badge).toHaveText('2');
    await expect(firstRow).toHaveAttribute('data-read-state', 'read');
    await expect(firstRow.getByText('Read', { exact: true })).toBeVisible();

    // --- Mark all: badge gone, every row read, no reload ------------------
    await page.getByRole('button', { name: 'Mark all read' }).click();

    await expect(badge).toHaveCount(0);
    await expect(page.getByText('You’re all caught up')).toBeVisible();
    await expect(rows.filter({ hasText: 'Unread' })).toHaveCount(0);
    for (const row of await rows.all()) {
      await expect(row).toHaveAttribute('data-read-state', 'read');
    }

    expect(new URL(page.url()).pathname).toBe('/notifications');
  });
});
