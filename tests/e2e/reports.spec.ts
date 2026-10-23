/**
 * Analytics & Reports e2e — the projection hop and the role-scoped surface.
 *
 * The reports worker runs in `full-hop.sh` beside the relay/realtime/payroll
 * workers; `full-hop.sh` publishes deterministic synthetic envelopes before it
 * starts. The fixture uses a synthetic employee in the seeded People Ops
 * department and fixed dates, so earlier specs' Backend facts cannot satisfy
 * these assertions and a rerun converges on the same rows.
 *
 * The spec waits on the API (bounded poll) rather than a fixed sleep: an empty
 * response is a projection that has not caught up yet, and the first HR view
 * must show the same exact fixture rows. The second half proves the access
 * boundary: the seeded employee role never sees the Reports nav, a direct visit
 * renders the API's 403 as the surface's denied state, and the API answers 403.
 */

import { expect, test, type APIRequestContext, type Browser } from "@playwright/test";

import { API_URL, HR_EMAIL, WORKER_EMAIL, apiLogin, uiLogin } from "./auth";

const OVERTIME_REPORT = "/api/v1/reports/overtime";
const ATTENDANCE_REPORT = "/api/v1/reports/attendance";
const LEAVE_REPORT = "/api/v1/reports/leave";
const HEADCOUNT_REPORT = "/api/v1/reports/headcount";

/** Must match `reset_reports_fixtures` in `full-hop.sh`. */
const FIXTURE_DEPARTMENT = "People Ops";
const FIXTURE_FROM = "2026-01-01";
const FIXTURE_TO = "2026-07-31";

type ReportRow = {
  bucket: string | null;
  groups: { label: string }[];
  verified_hours?: string;
  verified_request_count?: number;
  checkin_count?: number;
  worked_minutes?: number;
  approved_day_count?: number;
  headcount?: number;
};

function reportUrl(path: string, extra = ""): string {
  return `${path}?from=${FIXTURE_FROM}&to=${FIXTURE_TO}&group_by=department&interval=month${extra}`;
}

async function fetchRows(
  context: APIRequestContext,
  path: string,
): Promise<ReportRow[]> {
  const response = await context.get(reportUrl(path));
  if (response.status() !== 200) return [];
  const body = (await response.json()) as { rows?: ReportRow[] };
  return body.rows ?? [];
}

function fixtureRow(row: ReportRow): boolean {
  return row.groups.some((group) => group.label === FIXTURE_DEPARTMENT);
}

async function fixtureProjected(context: APIRequestContext): Promise<boolean> {
  const [overtime, attendance, leave, headcount] = await Promise.all([
    fetchRows(context, OVERTIME_REPORT),
    fetchRows(context, ATTENDANCE_REPORT),
    fetchRows(context, LEAVE_REPORT),
    fetchRows(context, HEADCOUNT_REPORT),
  ]);
  return (
    overtime.some(
      (row) =>
        fixtureRow(row) &&
        row.verified_hours === "2.50" &&
        row.verified_request_count === 1,
    ) &&
    attendance.some(
      (row) =>
        fixtureRow(row) && row.checkin_count === 1 && row.worked_minutes === 450,
    ) &&
    leave.some((row) => fixtureRow(row) && row.approved_day_count === 2) &&
    headcount.some((row) => fixtureRow(row) && row.headcount === 1)
  );
}

async function loggedIn(browser: Browser, email: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await uiLogin(page, email);
  return { context, page };
}

test.describe.serial("reports", () => {
  test("HR reads the projected reports; an employee is denied", async ({ browser }) => {
    // The worker may still be folding the fixtures when Playwright starts.
    // Wait for the fixture's exact metrics in all four families on the real
    // API — the same request the web surface makes — instead of sleeping.
    const hrApi = await apiLogin(HR_EMAIL);
    await expect
      .poll(() => fixtureProjected(hrApi), {
        message: "the reports worker projects the People Ops fixtures",
        timeout: 60_000,
        intervals: [250, 500, 1000],
      })
      .toBe(true);

    // --- HR: the surface shows the same fixture rows ---------------------
    const hr = await loggedIn(browser, HR_EMAIL);
    await hr.page.goto("/reports");
    await expect(
      hr.page.getByRole("heading", { name: "Reports", exact: true }),
    ).toBeVisible();

    // Pin the window to the fixture dates so the assertions stay stable as the
    // calendar moves; the default window is unit-tested in the web suite.
    const [rangeResponse] = await Promise.all([
      hr.page.waitForResponse(
        (candidate) =>
          candidate.request().method() === "GET" &&
          candidate.url().includes(OVERTIME_REPORT) &&
          candidate.url().includes(`from=${FIXTURE_FROM}`) &&
          candidate.url().includes(`to=${FIXTURE_TO}`),
      ),
      (async () => {
        await hr.page.getByLabel("From date").fill(FIXTURE_FROM);
        await hr.page.getByLabel("To date").fill(FIXTURE_TO);
      })(),
    ]);
    expect(rangeResponse.status(), await rangeResponse.text()).toBe(200);

    const overtimeTable = hr.page.getByTestId("reports-table");
    const overtimeRow = overtimeTable
      .getByRole("row")
      .filter({ hasText: FIXTURE_DEPARTMENT })
      .first();
    await expect(overtimeRow).toBeVisible();
    await expect(overtimeRow.getByTestId("report-metric-verified_hours")).toHaveText(
      "2.50",
    );

    // Switching tabs refetches the newly visible report and keeps the fixed
    // window; each tab shows its own fixture metric.
    for (const [tab, path, metricId, expected] of [
      ["Attendance", ATTENDANCE_REPORT, "report-metric-worked_minutes", "7h 30m"],
      ["Leave", LEAVE_REPORT, "report-metric-approved_day_count", "2"],
      ["Headcount", HEADCOUNT_REPORT, "report-metric-headcount", "1"],
    ] as const) {
      const [response] = await Promise.all([
        hr.page.waitForResponse(
          (candidate) =>
            candidate.request().method() === "GET" &&
            candidate.url().includes(path) &&
            candidate.url().includes(`from=${FIXTURE_FROM}`),
        ),
        hr.page.getByRole("tab", { name: tab, exact: true }).click(),
      ]);
      expect(response.status(), await response.text()).toBe(200);
      const tabRow = hr.page
        .getByTestId("reports-table")
        .getByRole("row")
        .filter({ hasText: FIXTURE_DEPARTMENT })
        .first();
      await expect(tabRow).toBeVisible();
      await expect(tabRow.getByTestId(metricId)).toHaveText(expected);
    }

    // A filter change is a new query: the interval select re-asks the API.
    await hr.page.getByRole("tab", { name: "Overtime", exact: true }).click();
    const [totalResponse] = await Promise.all([
      hr.page.waitForResponse(
        (candidate) =>
          candidate.request().method() === "GET" &&
          candidate.url().includes(OVERTIME_REPORT) &&
          candidate.url().includes("interval=total"),
      ),
      (async () => {
        await hr.page.getByLabel("Interval").click();
        await hr.page.getByRole("option", { name: "Whole range" }).click();
      })(),
    ]);
    expect(totalResponse.status(), await totalResponse.text()).toBe(200);
    await expect(hr.page.getByText("Total", { exact: true }).first()).toBeVisible();

    await hr.context.close();

    // --- Employee: no nav entry, a denied direct visit, an API 403 --------
    const worker = await loggedIn(browser, WORKER_EMAIL);
    await expect(
      worker.page
        .getByRole("navigation", { name: "Main" })
        .getByRole("link", { name: "Reports" }),
    ).toHaveCount(0);

    await worker.page.goto("/reports");
    await expect(
      worker.page.getByRole("heading", { name: "Reports", exact: true }),
    ).toBeVisible();
    await expect(
      worker.page.getByText("You do not have access to reports."),
    ).toBeVisible();
    await expect(worker.page.getByTestId("reports-table")).toHaveCount(0);

    // `page.request` has no API base URL (the browser is on :3000), so the
    // absolute URL is required — the 403 is the API policy, not a 404 route.
    const denied = await worker.page.request.get(
      `${API_URL}${reportUrl(OVERTIME_REPORT)}`,
    );
    expect(denied.status()).toBe(403);

    await worker.context.close();
    await hrApi.dispose();
  });
});
