/**
 * Payroll e2e (T12 / AC15, AC13, AC14) — the full self-service hop, plus the
 * overtime seam (overtime T8 / ADR 0018):
 *
 * 1. Finance configures the tenant inputs through the UI, adds salary records
 *    for the seeded worker and manager, starts a run for the last closed month,
 *    and sees the background worker take it from `queued` to `calculated`.
 *    Before the run starts, an `hr_verified` overtime claim for that month is
 *    seeded through the real worker → manager → HR API chain, so the run's
 *    timesheet must carry its hours (the seam read port).
 * 2. The run detail exposes the worker's provisional payslip line by line; a
 *    correction-free finalize issues it, flips the row to `Issued`, and flips
 *    the overtime row to `included_in_payroll` with the run handle (the seam
 *    write port).
 * 3. The worker logs in, sees only their own issued payslip under "My
 *    payslips", and a foreign payslip id renders the not-found state. Their
 *    overtime history shows the same claim as `included_in_payroll`.
 *
 * `full-hop.sh` resets the seeded tenant's payroll ledger and overtime rows
 * before Playwright starts, so the expected net and the absence of other
 * issued payslips are deterministic. The spec repeats both resets before its
 * finance test so a Playwright retry starts clean too. The queued finalize
 * gate is pinned by holding the run-detail GET at its real `queued` response
 * once, then releasing it to the worker.
 */

import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { expect, test, type Route } from "@playwright/test";

import {
  API_URL,
  FINANCE_EMAIL,
  HR_EMAIL,
  MANAGER_EMAIL,
  MANAGER_EMPLOYEE_ID,
  WORKER_EMAIL,
  WORKER_EMPLOYEE_ID,
  apiLogin,
  uiLogin,
} from "./auth";

const SALARY_EFFECTIVE_FROM = "2026-01-01";
const WORKER_BASE = "4800";
const MANAGER_BASE = "6000";
const DEDUCTION_CODE = "E2E_TAX";
/** Worker net with 4 overtime hours: 4,800 + 180 − 480 = 4,500.00. */
const WORKER_NET = "4,500.00";
/** Worker overtime pay: 4h × (4,800 / 160) × 1.5 = 180.00. */
const WORKER_OVERTIME_PAY = "180.00";
const SEAM_OVERTIME_HOURS = 4;

const OVERTIME_REQUESTS_URL = `${API_URL}/api/v1/overtime/requests`;

const API_DIR = join(process.cwd(), "apps/api");
const RESET_SCRIPT = join(API_DIR, "scripts", "reset_payroll_fixtures.py");
const OVERTIME_RESET_SCRIPT = join(API_DIR, "scripts", "reset_overtime_fixtures.py");
const API_PYTHON = join(API_DIR, ".venv", "bin", "python");

let period = "";
let runId = "";
let foreignPayslipId = "";
let overtimeRequestId = "";

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

/** Retry-safe overtime slate: the seam row must be the only verified one. */
function resetOvertimeFixtures(): void {
  execFileSync(API_PYTHON, [OVERTIME_RESET_SCRIPT], {
    cwd: API_DIR,
    env: { ...process.env, PYTHONPATH: "../..:." },
    stdio: "pipe",
  });
}

/**
 * Seed one `hr_verified` overtime claim for the run's period through the real
 * API chain. Overtime owns the row; payroll only consumes it through the
 * `OvertimeHoursPort` seam (ADR 0018).
 */
async function seedVerifiedOvertime(forPeriod: string): Promise<void> {
  const worker = await apiLogin(WORKER_EMAIL);
  const manager = await apiLogin(MANAGER_EMAIL);
  const hr = await apiLogin(HR_EMAIL);
  try {
    const created = await worker.post(OVERTIME_REQUESTS_URL, {
      data: {
        work_date: `${forPeriod}-15`,
        hours: SEAM_OVERTIME_HOURS,
        reason: "E2E payroll seam fixture",
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const request = (await created.json()) as { id: string; status: string };
    expect(request.status).toBe("requested");
    overtimeRequestId = request.id;

    const approved = await manager.post(
      `${OVERTIME_REQUESTS_URL}/${request.id}/approve`,
      { data: { note: null } },
    );
    expect(approved.status(), await approved.text()).toBe(200);
    expect(((await approved.json()) as { status: string }).status).toBe(
      "manager_approved",
    );

    const verified = await hr.post(`${OVERTIME_REQUESTS_URL}/${request.id}/verify`, {
      data: { note: null },
    });
    expect(verified.status(), await verified.text()).toBe(200);
    expect(((await verified.json()) as { status: string }).status).toBe("hr_verified");
  } finally {
    await worker.dispose();
    await manager.dispose();
    await hr.dispose();
  }
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
    resetOvertimeFixtures();

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
    // The seam fixture must exist before the worker calculates the run.
    await seedVerifiedOvertime(period);
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
    const timesheetTable = page.getByRole("table", { name: "Run timesheets" });
    await expect(timesheetTable).toBeVisible();
    const workerTimesheet = timesheetTable
      .getByRole("row")
      .filter({ hasText: "Employee …0021" });
    // Column 3 (0-based: employee, worked, overtime) is the seam read.
    await expect(workerTimesheet.locator("td").nth(2)).toHaveText(
      String(SEAM_OVERTIME_HOURS),
    );

    const payslipTable = page.getByRole("table", { name: "Run payslips" });
    await expect(payslipTable).toBeVisible();
    const workerRow = payslipTable.getByRole("row").filter({ hasText: "Employee …0021" });
    await expect(workerRow).toContainText("Provisional");
    await expect(workerRow).toContainText(`$${WORKER_NET}`);

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

    // --- The finalize flip is payroll's accounting fact (ADR 0018) ---------
    expect(overtimeRequestId).not.toBe("");
    const workerApi = await apiLogin(WORKER_EMAIL);
    try {
      const flipped = await workerApi.get(
        `${OVERTIME_REQUESTS_URL}/${overtimeRequestId}`,
      );
      expect(flipped.status(), await flipped.text()).toBe(200);
      const overtimeRow = (await flipped.json()) as {
        status: string;
        included_in_run_id: string | null;
      };
      expect(overtimeRow.status).toBe("included_in_payroll");
      expect(overtimeRow.included_in_run_id).toBe(runId);
    } finally {
      await workerApi.dispose();
    }

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
    await expect(page.getByText(WORKER_OVERTIME_PAY)).toBeVisible();
    await expect(page.getByText(WORKER_NET)).toBeVisible();
    await expect(page.getByText(DEDUCTION_CODE)).toBeVisible();

    // A foreign payslip id is the same not-found state as an unknown id.
    await page.goto(`/payroll/my-payslips/${foreignPayslipId}`);
    await expect(
      page.getByText("This payslip doesn’t exist or you don’t have access to it."),
    ).toBeVisible();

    // The finalize flip is visible on the employee's own overtime history.
    await page.goto("/overtime");
    await expect(
      page.getByRole("heading", { name: "Overtime", exact: true }),
    ).toBeVisible();
    const overtimeRow = page
      .getByTestId("my-overtime-row")
      .filter({ hasText: "Included in payroll" });
    await expect(overtimeRow).toHaveCount(1);
    await expect(overtimeRow).toHaveAttribute("data-status", "included_in_payroll");
  });
});
