/**
 * Employee documents e2e — the full hop across two roles (employee-documents
 * AC16): HR uploads a file through the real two-step presigned flow on the
 * employee detail, the employee sees it under "My documents" and downloads it
 * through a fresh presigned URL, then HR deletes it and both surfaces and the
 * direct download endpoint agree it is gone.
 *
 * Runs against the seeded dev database and the dev MinIO bucket (`make
 * check-e2e` brings both up). The document title is namespaced per run so a
 * retry never collides with a leftover from an interrupted earlier run; the
 * flow deletes its own document, so repeat runs start clean.
 */

import { expect, test, type Locator, type Page } from '@playwright/test';

import {
  API_URL,
  HR_EMAIL,
  WORKER_EMAIL,
  WORKER_EMPLOYEE_ID,
  uiLogin,
} from './auth';

const RUN = Date.now().toString(36);
const TITLE = `E2E certification ${RUN}`;
/** A future calendar date so the row proves a real expiry, never the badge. */
const EXPIRY = new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
const EXPIRY_LABEL = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
}).format(new Date(`${EXPIRY}T00:00:00Z`));
const FIXTURE = 'tests/e2e/fixtures/document.png';

/** Radix selects render their options in a body portal once the trigger opens. */
async function selectOption(page: Page, scope: Locator, label: string, option: string) {
  await scope.getByLabel(label).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

test('HR uploads, the employee downloads, HR deletes', async ({ browser, page }) => {
  // --- HR uploads through the employee detail's Documents tab ---------------
  await uiLogin(page, HR_EMAIL);
  await page.goto(`/employees/${WORKER_EMPLOYEE_ID}`);
  await page.getByRole('tab', { name: 'Documents' }).click();
  await page.getByRole('button', { name: 'Upload document' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Upload document' })).toBeVisible();
  await dialog.getByLabel('File').setInputFiles(FIXTURE);
  await dialog.getByLabel('Title').fill(TITLE);
  await selectOption(page, dialog, 'Category', 'Certification');
  await dialog.getByLabel('Expiry date').fill(EXPIRY);

  // Capture the confirm call (the storage POST targets the MinIO bucket, so
  // the filter pins the API origin and exact documents collection path).
  const confirmResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === 'POST' &&
      response.url().startsWith(API_URL) &&
      url.pathname === `/api/v1/employees/${WORKER_EMPLOYEE_ID}/documents`
    );
  });
  await dialog.getByRole('button', { name: 'Upload', exact: true }).click();
  const confirmed = await confirmResponse;
  expect(confirmed.status(), await confirmed.text()).toBe(201);
  const document = (await confirmed.json()) as { id: string; title: string };
  expect(document.title).toBe(TITLE);

  await expect(dialog).toBeHidden();
  const hrRow = page.getByRole('row').filter({ hasText: TITLE });
  await expect(hrRow).toBeVisible();
  await expect(hrRow).toContainText('Certification');
  await expect(hrRow).toContainText(EXPIRY_LABEL);
  await expect(hrRow.getByText('Expired')).toHaveCount(0);

  // --- The employee sees it under "My documents" and downloads it -----------
  const workerContext = await browser.newContext();
  try {
    const workerPage = await workerContext.newPage();
    // The download opens a new tab; close it so it never outlives the hop.
    workerContext.on('page', (popup) => {
      if (popup !== workerPage) void popup.close();
    });

    await uiLogin(workerPage, WORKER_EMAIL);
    await workerPage.goto('/profile');
    await expect(workerPage.getByRole('heading', { name: 'My documents', level: 1 })).toBeVisible();

    const workerRow = workerPage.getByRole('row').filter({ hasText: TITLE });
    await expect(workerRow).toBeVisible();
    await expect(workerRow).toContainText('Certification');
    await expect(workerRow.getByText('Expired')).toHaveCount(0);

    const downloadResponse = workerPage.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === 'GET' &&
        url.pathname ===
          `/api/v1/employees/${WORKER_EMPLOYEE_ID}/documents/${document.id}/download`
      );
    });
    await workerPage.getByRole('button', { name: `Download ${TITLE}` }).click();
    const minted = await downloadResponse;
    expect(minted.status(), await minted.text()).toBe(200);
    const { url } = (await minted.json()) as { url: string };
    expect(url).toContain('127.0.0.1:9000');

    // The presigned URL is the download: it resolves to the private object.
    const object = await workerContext.request.get(url);
    expect(object.status(), await object.text()).toBe(200);
    expect(object.headers()['content-type']).toContain('image/png');

    // --- HR deletes it; both surfaces and a direct download agree -------------
    await expect(hrRow).toBeVisible();
    await hrRow.getByRole('button', { name: `Delete ${TITLE}` }).click();
    const deleteDialog = page.getByRole('dialog');
    await expect(deleteDialog.getByRole('heading', { name: 'Delete document' })).toBeVisible();

    const deleteResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === 'DELETE' &&
        url.pathname ===
          `/api/v1/employees/${WORKER_EMPLOYEE_ID}/documents/${document.id}`
      );
    });
    await deleteDialog.getByRole('button', { name: 'Delete document' }).click();
    const deleted = await deleteResponse;
    expect(deleted.status()).toBe(204);

    await expect(deleteDialog).toBeHidden();
    await expect(page.getByRole('row').filter({ hasText: TITLE })).toHaveCount(0);

    await workerPage.reload();
    await expect(workerPage.getByRole('heading', { name: 'My documents', level: 1 })).toBeVisible();
    await expect(workerPage.getByText(TITLE)).toHaveCount(0);

    const gone = await workerContext.request.get(
      `${API_URL}/api/v1/employees/${WORKER_EMPLOYEE_ID}/documents/${document.id}/download`,
    );
    expect(gone.status(), await gone.text()).toBe(404);
    expect(((await gone.json()) as { error: { code: string } }).error.code).toBe(
      'document_not_found',
    );
  } finally {
    await workerContext.close();
  }
});
