import { defineConfig, devices } from '@playwright/test';

/**
 * E2E gate — cross-surface flows in `tests/e2e/` (run via `make check-e2e`).
 *
 * `make check-e2e` brings up PostgreSQL, applies migrations and seeds the dev
 * database; this config then owns both dev servers: FastAPI on :8000 (reads
 * `apps/api/.env`) and Next.js on :3000 (dev-identity + API URL defaults from
 * `packages/api-client`). Chromium only while the suite is young — a slow or
 * flaky gate gets ignored (`.ai/testing.md` decision 3), so the suite stays
 * out of `make check` until it has earned its place there.
 */
export default defineConfig({
  testDir: './tests/e2e',
  /* One worker: the flow mutates shared state and reads it back. */
  fullyParallel: false,
  workers: 1,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  /* No HTML report artifacts in the repo (test-results/ is gitignored anyway). */
  reporter: 'list',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: [
    {
      command: 'uv run uvicorn app.main:app --port 8000',
      cwd: 'apps/api',
      url: 'http://localhost:8000/healthz',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'pnpm run dev',
      cwd: 'apps/web',
      url: 'http://localhost:3000/employees',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
  ],
});
