/**
 * Payroll e2e (T12 / AC15, AC13, AC14) — the full self-service hop:
 *
 * 1. Finance configures the tenant inputs through the UI, adds salary records
 *    for the seeded worker and manager, starts a run for the last closed month,
 *    and sees the background worker take it from `queued` to `calculated`.
 * 2. The run detail exposes the worker's provisional payslip line by line; a
 *    correction-free finalize issues it and flips the row to `Issued`.
 * 3. The worker logs in, sees only their own issued payslip under "My
 *    payslips", and a foreign payslip id renders the not-found state.
 *
 * `full-hop.sh` resets the seeded tenant's payroll ledger before Playwright
 * starts, so the expected net and the absence of other issued payslips are
 * deterministic. The queued finalize gate is pinned by holding the run-detail
 * GET at its real `queued` response once, then releasing it to the worker.
 */

import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { expect, test, type Route } from "@playwright/test";

import {
  API_URL,
  FINANCE_EMAIL,
  MANAGER_EMPLOYEE_ID,
  WORKER_EMAIL,
  WORKER_EMPLOYEE_ID,
  uiLogin,
} from "./auth";

const SALARY_EFFECTIVE_FROM = "2026-01-01";
const WORKER_BASE = "4800";
const MANAGER_BASE = "6000";
const DEDUCTION_CODE = "E2E_TAX";
const WORKER_NET = "4,320.00";

const API_DIR = join(process.cwd(), "apps/api");
const RESET_SCRIPT = join(API_DIR, "scripts", "reset_payroll_fixtures.py");
const API_PYTHON = join(API_DIR, ".venv", "bin", "python");

let period = "";
let runId = "";
let foreignPayslipId = "";

/**
 * A retry starts from a clean ledger. `full-hop.sh` resets once before
 * Playwright, but Playwright's CI retries re-enter this test without rerunning
 * the hop; the fixed deduction code and closed period would otherwise collide
 * with the first attempt's rows.
 */
function resetPayrollFixtures(): void {
  execFileSync(API_PYTHON, [RESET_SCRIPT], {
    cwd: API_DIR,
    env: { ...process.env, PYTHONPATH: "../..:." },
    stdio: "pipe",
  });
}

/** `2026-09` → `September 2026`, the UI's period label. */
function readablePeriod(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}-01T00:00:00Z`));
}

/** Hold the first real run-detail response at `queued` so the gate is provable. */
function queuedRunBody(body: Record<string, unknown>): Record<string, unknown> {
  if (body.status === "queued" || body.status === "calculating") return body;
  return {
    ...body,
    status: "queued",
    totals: null,
    failed_code: null,
    calculated_at: null,
    finalized_at: null,
  };
}

test.describe.serial("payroll", () => {
  test("finance configures inputs, calculates, reviews and finalizes", async ({ page }) => {
    resetPayrollFixtures();

    await uiLogin(page, FINANCE_EMAIL);
    await page.goto("/payroll");
    await expect(page.getByRole("heading", { name: "Payroll", exact: true })).toBeVisible();

    // --- Configuration -----------------------------------------------------
    await page.getByLabel("Currency").fill("USD");
    await page.getByLabel("Standard monthly hours").fill("160");
    await page.getByLabel("Overtime multiplier").fill("1.50");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Payroll configuration saved")).toBeVisible();

    // --- One transparent deduction line ------------------------------------
    await page.getByRole("button", { name: "New deduction line" }).click();
    const deductionDialog = page.getByRole("dialog");
    await expect(
      deductionDialog.getByRole("heading", { name: "New deduction line" }),
    ).toBeVisible();
    await deductionDialog.getByLabel("Code").fill(DEDUCTION_CODE);
    await deductionDialog.getByLabel("Name").fill("E2E withholding");
    await deductionDialog.getByLabel("Value").fill("10");
    await deductionDialog.getByRole("button", { name: "Create deduction line" }).click();
    await expect(deductionDialog).toBeHidden();
    await expect(page.getByRole("row").filter({ hasText: DEDUCTION_CODE })).toBeVisible();

    // --- Salary records for the worker and the manager ---------------------
    // Finance cannot read the employee directory, so the form accepts the
    // employee UUID directly (the same API target the picker would send).
    await page.getByLabel("Employee ID").fill(WORKER_EMPLOYEE_ID);
    await page.getByLabel("Monthly base salary").fill(WORKER_BASE);
    await page.getByLabel("Effective from").fill(SALARY_EFFECTIVE_FROM);
    await page.getByRole("button", { name: "Add salary record" }).click();
    await expect(
      page.getByRole("row").filter({ hasText: "Employee …0021" }),
    ).toBeVisible();

    await page.getByLabel("Employee ID").fill(MANAGER_EMPLOYEE_ID);
    await page.getByLabel("Monthly base salary").fill(MANAGER_BASE);
    await page.getByLabel("Effective from").fill(SALARY_EFFECTIVE_FROM);
    await page.getByRole("button", { name: "Add salary record" }).click();
    await expect(
      page.getByRole("row").filter({ hasText: "Employee …0023" }),
    ).toBeVisible();

    // --- Start the run; pin the queued gate before releasing the worker ----
    let holdQueued = true;
    const runDetailPattern = /\/api\/v1\/payroll\/runs\/[0-9a-f-]+$/;
    await page.route(runDetailPattern, async (route: Route) => {
      if (!holdQueued || route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      const body = (await response.json()) as Record<string, unknown>;
      await route.fulfill({ response, json: queuedRunBody(body) });
    });

    await page.getByRole("button", { name: "New run" }).click();
    const runDialog = page.getByRole("dialog");
    period = await runDialog.getByLabel("Month").inputValue();
    expect(period).toMatch(/^\d{4}-(0[1-9]|1[0-2])$/);
    await runDialog.getByRole("button", { name: "Start run" }).click();
    await page.waitForURL(/\/payroll\/runs\/[0-9a-f-]+$/);

    const finalizeButton = page.getByRole("button", { name: "Finalize run" });
    await expect(finalizeButton).toBeDisabled();
    await expect(
      page.getByText("Finalize becomes available once the calculation finishes."),
    ).toBeVisible();

    holdQueued = false;
    await page.unroute(runDetailPattern);
    await page.reload();
    await expect(page.getByText("Calculated", { exact: true })).toBeVisible();
    await expect(finalizeButton).toBeEnabled();
    runId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(runId).not.toBe("");

    // --- Review the provisional payslip -----------------------------------
    const payslipTable = page.getByRole("table", { name: "Run payslips" });
    await expect(payslipTable).toBeVisible();
    const workerRow = payslipTable.getByRole("row").filter({ hasText: "Employee …0021" });
    await expect(workerRow).toContainText("Provisional");
    await expect(workerRow).toContainText("$4,320.00");

    await workerRow.getByRole("button", { name: /Show payslip lines for Employee/ }).click();
    const deductionRow = page.getByRole("row").filter({ hasText: DEDUCTION_CODE });
    await expect(deductionRow).toBeVisible();
    await expect(deductionRow).toContainText("$480.00");

    // --- Finalize: the row flips to issued --------------------------------
    await finalizeButton.click();
    const finalizeDialog = page.getByRole("dialog");
    await expect(
      finalizeDialog.getByRole("heading", { name: "Finalize payroll run?" }),
    ).toBeVisible();
    await finalizeDialog.getByRole("button", { name: "Finalize payroll" }).click();
    await expect(page.getByText("Finalized", { exact: true })).toBeVisible();
    await expect(workerRow).toContainText("Issued");

    // Capture the manager's payslip id for the employee's foreign-id check.
    const payslipsResponse = await page.request.get(
      `${API_URL}/api/v1/payroll/runs/${runId}/payslips?page_size=100`,
    );
    expect(payslipsResponse.status(), await payslipsResponse.text()).toBe(200);
    const { items } = (await payslipsResponse.json()) as {
      items: { id: string; employee_id: string; status: string }[];
    };
    const foreign = items.find(
      (item) => item.employee_id === MANAGER_EMPLOYEE_ID && item.status === "issued",
    );
    expect(foreign).toBeTruthy();
    foreignPayslipId = foreign?.id ?? "";
    expect(foreignPayslipId).not.toBe("");
  });

  test("employee sees only their issued payslip and no foreign payslip", async ({ page }) => {
    expect(foreignPayslipId).not.toBe("");

    await uiLogin(page, WORKER_EMAIL);

    // "My payslips" is visible to every role; the workspace is not.
    await expect(page.getByRole("link", { name: "My payslips" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Payroll", exact: true })).toHaveCount(0);
    await page.getByRole("link", { name: "My payslips" }).click();
    await expect(page).toHaveURL(/\/payroll\/my-payslips$/);
    await expect(page.getByRole("heading", { name: "My payslips", exact: true })).toBeVisible();

    const row = page.getByTestId("my-payslip-row").filter({ hasText: WORKER_NET });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("Issued");
    await expect(row).toContainText(readablePeriod(period));

    await row.getByRole("link", { name: /view issued .* payslip/i }).click();
    await expect(
      page.getByRole("heading", { name: `Payslip — ${readablePeriod(period)}` }),
    ).toBeVisible();
    await expect(page.getByText("Issued", { exact: true })).toBeVisible();
    await expect(page.getByText("4,800.00")).toBeVisible();
    await expect(page.getByText(WORKER_NET)).toBeVisible();
    await expect(page.getByText(DEDUCTION_CODE)).toBeVisible();

    // A foreign payslip id is the same not-found state as an unknown id.
    await page.goto(`/payroll/my-payslips/${foreignPayslipId}`);
    await expect(
      page.getByText("This payslip doesn’t exist or you don’t have access to it."),
    ).toBeVisible();
  });
});
