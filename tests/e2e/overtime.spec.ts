/**
 * Overtime e2e — the cross-surface promise of the feature (T8):
 *
 * 1. The seeded worker submits a claim through `/overtime`; it lands in "My
 *    requests" as `requested`. A second claim on the same work date is refused
 *    with the pinned `409 overtime_overlaps` message rendered on the field.
 * 2. The manager approves it from the manager inbox. The decision dialog is
 *    held open while the same session approves through the API, so its submit
 *    lands on the wrong state and renders the pinned
 *    `overtime_not_requested` message. The worker's open page then flips to
 *    `manager_approved` through list polling — no reload.
 * 3. HR verifies the claim through the HR queue; the row appears in HR's
 *    verified section and flips to `hr_verified` on the worker's still-open
 *    page (polling, bounded wait). HR then un-verifies it (the ADR 0018
 *    finalize-rollback window) and a stale reject dialog renders the pinned
 *    `overtime_not_open` message.
 * 4. The manager's own claim is listed without action buttons (the surfaces
 *    withhold self-decision); the direct API attempt from the same session
 *    returns the pinned `forbidden` code, and the claim is withdrawn through
 *    the UI.
 *
 * An approved or verified claim is terminal (no delete API), so `full-hop.sh`
 * resets the seeded worker's and manager's overtime rows before Playwright
 * starts, and this spec repeats that reset so a Playwright retry starts clean
 * against the same dev database (`.ai/testing.md` rule 6 — synthetic data
 * only).
 */

import { execFileSync } from "node:child_process";
import { join } from "node:path";

import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

import { API_URL, HR_EMAIL, MANAGER_EMAIL, WORKER_EMAIL, uiLogin } from "./auth";

const WORKER_NAME = "Wendy Worker";
const MANAGER_NAME = "Morgan Manager";
const OVERTIME_REQUESTS_URL = `${API_URL}/api/v1/overtime/requests`;
const WORKER_REASON = "E2E overtime claim";
const MANAGER_REASON = "E2E manager own claim";

const API_DIR = join(process.cwd(), "apps/api");
const RESET_SCRIPT = join(API_DIR, "scripts", "reset_overtime_fixtures.py");
const API_PYTHON = join(API_DIR, ".venv", "bin", "python");

/** The server's UTC workday (the API's submit rule and the form's max). */
function utcDate(offsetDays = 0): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

/**
 * A retry starts from a clean overtime slate. `full-hop.sh` resets once before
 * Playwright, but a CI retry re-enters this test without rerunning the hop.
 */
function resetOvertimeFixtures(): void {
  execFileSync(API_PYTHON, [RESET_SCRIPT], {
    cwd: API_DIR,
    env: { ...process.env, PYTHONPATH: "../..:." },
    stdio: "pipe",
  });
}

/** Fresh context + real UI login — independent cookie jars per role. */
async function loggedIn(
  browser: Browser,
  email: string,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await uiLogin(page, email);
  return { context, page };
}

async function submitClaim(
  page: Page,
  dialog: Locator,
  workDate: string,
  hours: string,
  reason: string,
) {
  await dialog.getByLabel("Work date").fill(workDate);
  await dialog.getByLabel("Hours").fill(hours);
  await dialog.getByLabel("Reason (optional)").fill(reason);
  return Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url() === OVERTIME_REQUESTS_URL,
    ),
    dialog.getByRole("button", { name: "Submit request" }).click(),
  ]);
}

test.describe.serial("overtime", () => {
  test("request → approve → verify on both views, with the pinned refusals", async ({
    browser,
  }) => {
    resetOvertimeFixtures();

    const worker = await loggedIn(browser, WORKER_EMAIL);
    const manager = await loggedIn(browser, MANAGER_EMAIL);
    const hr = await loggedIn(browser, HR_EMAIL);
    const today = utcDate();

    // --- Worker: submit through the real UI -------------------------------
    await worker.page.goto("/overtime");
    await expect(
      worker.page.getByRole("heading", { name: "Overtime", exact: true }),
    ).toBeVisible();
    await worker.page.getByRole("button", { name: "Request overtime" }).click();

    let dialog = worker.page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "Request overtime" })).toBeVisible();
    const [created] = await submitClaim(worker.page, dialog, today, "2.5", WORKER_REASON);
    expect(created.status(), await created.text()).toBe(201);
    const request = (await created.json()) as {
      id: string;
      status: string;
      work_date: string;
      hours: number;
    };
    expect(request.status).toBe("requested");
    expect(request.work_date).toBe(today);
    expect(request.hours).toBe(2.5);
    await expect(dialog).toBeHidden();

    const workerRow = worker.page
      .getByTestId("my-overtime-row")
      .filter({ hasText: WORKER_REASON });
    await expect(workerRow).toHaveAttribute("data-status", "requested");

    // --- Failure case: a second claim on the same work date overlaps ------
    await worker.page.getByRole("button", { name: "Request overtime" }).click();
    dialog = worker.page.getByRole("dialog");
    const [overlap] = await submitClaim(worker.page, dialog, today, "1", "Second claim");
    expect(overlap.status(), await overlap.text()).toBe(409);
    await expect(
      dialog.getByText("You already have an open overtime request for that date."),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();

    // --- Manager: approve, and prove the wrong-state refusal on a stale
    // dialog. The direct API call is the same authenticated session, so the
    // button below is a real attempt against a row that already moved.
    await manager.page.goto("/overtime");
    const approvalRow = manager.page
      .getByTestId("manager-inbox-row")
      .filter({ hasText: WORKER_NAME });
    await expect(approvalRow).toBeVisible();
    await approvalRow
      .getByRole("button", { name: `Approve overtime request for ${WORKER_NAME}` })
      .click();

    const decision = manager.page.getByRole("dialog");
    await expect(
      decision.getByRole("heading", { name: "Approve overtime request" }),
    ).toBeVisible();
    await decision.getByLabel("Decision note (optional)").fill("Approved via e2e.");

    const approvedDirectly = await manager.page.request.post(
      `${OVERTIME_REQUESTS_URL}/${request.id}/approve`,
      { data: { note: "Approved via e2e." } },
    );
    expect(approvedDirectly.status(), await approvedDirectly.text()).toBe(200);
    expect(((await approvedDirectly.json()) as { status: string }).status).toBe(
      "manager_approved",
    );

    const [staleApprove] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url() === `${OVERTIME_REQUESTS_URL}/${request.id}/approve`,
      ),
      decision.getByRole("button", { name: "Approve request" }).click(),
    ]);
    expect(staleApprove.status(), await staleApprove.text()).toBe(409);
    await expect(
      decision.getByText(
        "This request is no longer waiting for manager approval. Refresh and try again.",
      ),
    ).toBeVisible();
    await decision.getByRole("button", { name: "Cancel" }).click();
    await expect(decision).toBeHidden();

    // The worker's open page follows the decision without a reload.
    await expect(workerRow).toHaveAttribute("data-status", "manager_approved", {
      timeout: 10_000,
    });

    // --- Self-decision: the manager's own claim is listed without actions.
    // A real attempt from the same session is refused with the pinned code.
    await manager.page.getByRole("button", { name: "Request overtime" }).click();
    dialog = manager.page.getByRole("dialog");
    const [ownCreated] = await submitClaim(manager.page, dialog, today, "1", MANAGER_REASON);
    expect(ownCreated.status(), await ownCreated.text()).toBe(201);
    const ownRequest = (await ownCreated.json()) as { id: string; status: string };
    expect(ownRequest.status).toBe("requested");
    await expect(dialog).toBeHidden();

    const ownInboxRow = manager.page
      .getByTestId("manager-inbox-row")
      .filter({ hasText: MANAGER_NAME });
    await expect(ownInboxRow).toBeVisible();
    await expect(ownInboxRow).toContainText(
      "Your own request — someone else must decide.",
    );
    await expect(ownInboxRow.getByRole("button")).toHaveCount(0);

    const selfApprove = await manager.page.request.post(
      `${OVERTIME_REQUESTS_URL}/${ownRequest.id}/approve`,
      { data: { note: null } },
    );
    expect(selfApprove.status(), await selfApprove.text()).toBe(403);
    expect((await selfApprove.json()) as { error: { code: string } }).toMatchObject({
      error: { code: "forbidden" },
    });

    const ownRequestRow = manager.page
      .getByTestId("my-overtime-row")
      .filter({ hasText: MANAGER_REASON });
    await expect(ownRequestRow).toHaveAttribute("data-status", "requested");
    const [withdrawn] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url() === `${OVERTIME_REQUESTS_URL}/${ownRequest.id}/withdraw`,
      ),
      ownRequestRow
        .getByRole("button", { name: `Withdraw overtime request on ${today}` })
        .click(),
    ]);
    expect(withdrawn.status(), await withdrawn.text()).toBe(200);
    await expect(ownRequestRow).toHaveAttribute("data-status", "withdrawn");

    // --- HR: verify through the queue; the row lands on both views ---------
    await hr.page.goto("/overtime");
    const pendingRow = hr.page
      .locator('[data-testid="hr-verification-row"][data-status="manager_approved"]')
      .filter({ hasText: WORKER_NAME });
    await expect(pendingRow).toBeVisible();
    await pendingRow
      .getByRole("button", { name: `Verify overtime request for ${WORKER_NAME}` })
      .click();

    const verification = hr.page.getByRole("dialog");
    await expect(
      verification.getByRole("heading", { name: "Verify overtime request" }),
    ).toBeVisible();
    await verification.getByLabel("Decision note (optional)").fill("Verified via e2e.");
    const [verified] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url() === `${OVERTIME_REQUESTS_URL}/${request.id}/verify`,
      ),
      verification.getByRole("button", { name: "Verify request" }).click(),
    ]);
    expect(verified.status(), await verified.text()).toBe(200);
    expect(((await verified.json()) as { status: string }).status).toBe("hr_verified");
    await expect(verification).toBeHidden();

    const verifiedRow = hr.page
      .locator('[data-testid="hr-verification-row"][data-status="hr_verified"]')
      .filter({ hasText: WORKER_NAME });
    await expect(verifiedRow).toBeVisible();
    await expect(verifiedRow).toContainText("HR verified");

    // The worker's open page follows the verification without a reload.
    await expect(workerRow).toHaveAttribute("data-status", "hr_verified", {
      timeout: 10_000,
    });

    // --- HR un-verifies (the ADR 0018 window) and the stale reject refuses -
    await verifiedRow
      .getByRole("button", { name: `Reject overtime request for ${WORKER_NAME}` })
      .click();
    const rejection = hr.page.getByRole("dialog");
    await expect(
      rejection.getByRole("heading", { name: "Reject overtime request" }),
    ).toBeVisible();
    await rejection.getByLabel("Decision note (optional)").fill("Un-verified via e2e.");

    const rejectedDirectly = await hr.page.request.post(
      `${OVERTIME_REQUESTS_URL}/${request.id}/reject`,
      { data: { note: "Un-verified via e2e." } },
    );
    expect(rejectedDirectly.status(), await rejectedDirectly.text()).toBe(200);
    const rejectedBody = (await rejectedDirectly.json()) as {
      status: string;
      rejected_from: string;
    };
    expect(rejectedBody.status).toBe("rejected");
    expect(rejectedBody.rejected_from).toBe("hr_verified");

    const [staleReject] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          response.url() === `${OVERTIME_REQUESTS_URL}/${request.id}/reject`,
      ),
      rejection.getByRole("button", { name: "Reject request" }).click(),
    ]);
    expect(staleReject.status(), await staleReject.text()).toBe(409);
    await expect(
      rejection.getByText(
        "This request can no longer be withdrawn or rejected. Refresh and try again.",
      ),
    ).toBeVisible();
    await rejection.getByRole("button", { name: "Cancel" }).click();
    await expect(rejection).toBeHidden();

    await hr.context.close();
    await manager.context.close();
    await worker.context.close();
  });
});
