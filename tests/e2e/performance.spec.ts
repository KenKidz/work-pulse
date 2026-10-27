/**
 * Performance e2e — the cross-surface promise of the feature (T13):
 *
 * 1. The seeded manager starts an ad-hoc review for the seeded worker (their
 *    direct report) through `/performance`, nominating the deterministic peer
 *    that `reset_performance_fixtures.py` creates — the seeds link only Wendy
 *    and Morgan, so a third active employee with a login is required.
 * 2. While the review is a draft the worker's "My reviews" shows no trace of
 *    it, and the Employee surface renders without review creation, team
 *    filters or catalog management (the read-only own-data role).
 * 3. Completing the draft without a rating is refused with the pinned
 *    `409 review_incomplete` sentence; the manager then edits in the rating and
 *    evaluative text.
 * 4. The peer signs in, files one comment from "Feedback requested", and a
 *    second submission is refused with the pinned
 *    `409 feedback_already_submitted` copy while the typed comment stays. The
 *    manager (author) reads the feedback on the draft.
 * 5. The manager completes the review, which publishes it: the worker's
 *    "My reviews" now lists it with the rating and sections — and never the
 *    peer's feedback text.
 *
 * Performance rows and the skill catalog are mutable through the UI, so
 * `full-hop.sh` resets the seeded tenant's fixtures before Playwright starts,
 * and this spec repeats that reset so a Playwright retry starts clean against
 * the same dev database (`.ai/testing.md` rule 6 — synthetic data only).
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

import { HR_EMAIL, MANAGER_EMAIL, PEER_EMAIL, WORKER_EMAIL, uiLogin } from "./auth";

const RUN = Date.now().toString(36);
const PERIOD = `E2E ${RUN}`;
const STRENGTHS = "Kept the release on track through the migration.";
const IMPROVEMENTS = "Bring the team into design reviews earlier.";
const PEER_COMMENT = "Wendy kept the migration moving and unblocked the team.";
const SECOND_COMMENT = "This second comment must be refused.";

const WORKER_NAME = "Wendy Worker";
const PEER_NAME = "Parker Peer";

const API_DIR = join(process.cwd(), "apps/api");
const RESET_SCRIPT = join(API_DIR, "scripts", "reset_performance_fixtures.py");
const API_PYTHON = join(API_DIR, ".venv", "bin", "python");

/** Port-agnostic path comparison for the API responses this spec waits on. */
function apiPath(url: string): string {
  return new URL(url).pathname;
}

/**
 * A retry starts from a clean performance slate. `full-hop.sh` resets once
 * before Playwright, but a CI retry re-enters this test without rerunning the
 * hop.
 */
function resetPerformanceFixtures(): void {
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

/** Radix selects render their options in a body portal once the trigger opens. */
async function selectOption(page: Page, scope: Locator, label: string, option: string) {
  await scope.getByLabel(label).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}

async function openReviewsTab(page: Page) {
  await page.goto("/performance");
  await expect(
    page.getByRole("heading", { name: "Performance", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Reviews", exact: true }).click();
}

test.describe.serial("performance", () => {
  test("manager review → peer feedback → complete → the employee sees the result", async ({
    browser,
  }) => {
    resetPerformanceFixtures();

    const manager = await loggedIn(browser, MANAGER_EMAIL);
    const worker = await loggedIn(browser, WORKER_EMAIL);
    const peer = await loggedIn(browser, PEER_EMAIL);

    // --- Manager: start a draft review and nominate the peer --------------
    await openReviewsTab(manager.page);
    await expect(manager.page.getByRole("button", { name: "New review" })).toBeVisible();

    await manager.page.getByRole("button", { name: "New review" }).click();
    const reviewDialog = manager.page.getByRole("dialog");
    await expect(
      reviewDialog.getByRole("heading", { name: "New review" }),
    ).toBeVisible();
    await selectOption(manager.page, reviewDialog, "Employee", WORKER_NAME);
    await reviewDialog.getByLabel("Period (optional)").fill(PERIOD);
    await reviewDialog.getByRole("button", { name: PEER_NAME, exact: true }).click();

    const [createdReview] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) === "/api/v1/performance/reviews",
      ),
      reviewDialog.getByRole("button", { name: "Create review" }).click(),
    ]);
    expect(createdReview.status(), await createdReview.text()).toBe(201);
    const review = (await createdReview.json()) as {
      id: string;
      status: string;
      feedback_provider_ids: string[];
    };
    expect(review.status).toBe("draft");
    expect(review.feedback_provider_ids).toHaveLength(1);
    await expect(reviewDialog).toBeHidden();

    let managerRow = manager.page.getByRole("row").filter({ hasText: PERIOD });
    await expect(managerRow.locator("[data-status='draft']")).toBeVisible();
    await expect(managerRow).toContainText(WORKER_NAME);

    // --- The draft is invisible to the subject, whose surface is own-only -
    await openReviewsTab(worker.page);
    await expect(
      worker.page.getByRole("heading", { name: "My reviews" }),
    ).toBeVisible();
    await expect(
      worker.page.getByText(
        "No completed reviews yet. Your manager's drafts stay hidden until they finish.",
      ),
    ).toBeVisible();
    await expect(worker.page.getByText(PERIOD)).toHaveCount(0);
    await expect(worker.page.getByRole("button", { name: "New review" })).toHaveCount(0);
    await expect(worker.page.getByLabel("Filter by employee")).toHaveCount(0);
    await expect(worker.page.getByText("Feedback requested")).toHaveCount(0);

    await worker.page.getByRole("tab", { name: "Skills", exact: true }).click();
    await expect(worker.page.getByLabel("Search the skill catalog")).toBeVisible();
    await expect(worker.page.getByRole("button", { name: "New skill" })).toHaveCount(0);

    // --- Failure path: completing a draft without a rating ----------------
    await managerRow
      .getByRole("button", { name: /^Complete the review for/ })
      .click();
    const stateDialog = manager.page.getByRole("dialog");
    await expect(
      stateDialog.getByRole("heading", { name: "Complete review" }),
    ).toBeVisible();
    const [refused] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) === `/api/v1/performance/reviews/${review.id}/complete`,
      ),
      stateDialog.getByRole("button", { name: "Complete review" }).click(),
    ]);
    expect(refused.status(), await refused.text()).toBe(409);
    await expect(
      stateDialog.getByText("Add a rating before completing the review."),
    ).toBeVisible();
    await stateDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(stateDialog).toBeHidden();

    // --- Manager: add the rating and evaluative text ----------------------
    await managerRow.getByRole("button", { name: /^Edit the review for/ }).click();
    const editDialog = manager.page.getByRole("dialog");
    await expect(editDialog.getByRole("heading", { name: "Edit review" })).toBeVisible();
    await selectOption(manager.page, editDialog, "Rating", "4 / 5");
    await editDialog.getByLabel("Strengths (optional)").fill(STRENGTHS);
    await editDialog.getByLabel("Improvements (optional)").fill(IMPROVEMENTS);

    const [updated] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === "PATCH" &&
          apiPath(response.url()) === `/api/v1/performance/reviews/${review.id}`,
      ),
      editDialog.getByRole("button", { name: "Save changes" }).click(),
    ]);
    expect(updated.status(), await updated.text()).toBe(200);
    await expect(editDialog).toBeHidden();

    // --- Peer: file feedback once, then see the pinned refusal -----------
    await openReviewsTab(peer.page);
    await expect(peer.page.getByText("Feedback requested")).toBeVisible();
    const giveFeedback = peer.page.getByRole("button", {
      name: /^Give feedback on the review for/,
    });
    await expect(giveFeedback).toHaveCount(1);

    await giveFeedback.click();
    let feedbackDialog = peer.page.getByRole("dialog");
    await expect(
      feedbackDialog.getByRole("heading", { name: "Give feedback" }),
    ).toBeVisible();
    await feedbackDialog.getByLabel("Comment").fill(PEER_COMMENT);
    const [submitted] = await Promise.all([
      peer.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) ===
            `/api/v1/performance/reviews/${review.id}/feedback`,
      ),
      feedbackDialog.getByRole("button", { name: "Submit feedback" }).click(),
    ]);
    expect(submitted.status(), await submitted.text()).toBe(201);
    await expect(feedbackDialog).toBeHidden();

    // The worklist keeps the row (the peer is still nominated while the
    // review is a draft), so the one-shot rule is provable through the UI.
    await expect(giveFeedback).toBeVisible();
    await giveFeedback.click();
    feedbackDialog = peer.page.getByRole("dialog");
    await feedbackDialog.getByLabel("Comment").fill(SECOND_COMMENT);
    const [duplicate] = await Promise.all([
      peer.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) ===
            `/api/v1/performance/reviews/${review.id}/feedback`,
      ),
      feedbackDialog.getByRole("button", { name: "Submit feedback" }).click(),
    ]);
    expect(duplicate.status(), await duplicate.text()).toBe(409);
    await expect(
      feedbackDialog.getByText("You already submitted feedback for this review."),
    ).toBeVisible();
    await expect(feedbackDialog.getByLabel("Comment")).toHaveValue(SECOND_COMMENT);
    await feedbackDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(feedbackDialog).toBeHidden();

    // --- Manager: the author reads the peer's comment, then completes -----
    await openReviewsTab(manager.page);
    managerRow = manager.page.getByRole("row").filter({ hasText: PERIOD });
    await managerRow.getByRole("button", { name: /^View the review for/ }).click();
    const detailDialog = manager.page.getByRole("dialog");
    await expect(
      detailDialog.getByRole("heading", { name: "Review details" }),
    ).toBeVisible();
    await expect(detailDialog.getByText(PEER_COMMENT)).toBeVisible();
    await expect(detailDialog.getByText("Peer feedback")).toBeVisible();
    await manager.page.keyboard.press("Escape");
    await expect(detailDialog).toBeHidden();

    await managerRow
      .getByRole("button", { name: /^Complete the review for/ })
      .click();
    const completeDialog = manager.page.getByRole("dialog");
    await expect(
      completeDialog.getByRole("heading", { name: "Complete review" }),
    ).toBeVisible();
    const [completed] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) === `/api/v1/performance/reviews/${review.id}/complete`,
      ),
      completeDialog.getByRole("button", { name: "Complete review" }).click(),
    ]);
    expect(completed.status(), await completed.text()).toBe(200);
    expect(((await completed.json()) as { status: string }).status).toBe("completed");
    await expect(completeDialog).toBeHidden();
    await expect(managerRow.locator("[data-status='completed']")).toBeVisible();

    // --- The subject now sees the completed review, never the feedback ----
    await openReviewsTab(worker.page);
    const myRow = worker.page.getByRole("row").filter({ hasText: PERIOD });
    await expect(myRow).toBeVisible();
    await expect(myRow.locator("[data-status='completed']")).toBeVisible();
    await expect(myRow.getByLabel("Rating 4 out of 5")).toBeVisible();

    await myRow.getByRole("button", { name: /^View the review for/ }).click();
    const workerDetail = worker.page.getByRole("dialog");
    await expect(
      workerDetail.getByRole("heading", { name: "Review details" }),
    ).toBeVisible();
    await expect(workerDetail.getByText(STRENGTHS)).toBeVisible();
    await expect(workerDetail.getByText(IMPROVEMENTS)).toBeVisible();
    await expect(workerDetail.getByText(PEER_COMMENT)).toHaveCount(0);
    await expect(workerDetail.getByText("Peer feedback")).toHaveCount(0);
    await worker.page.keyboard.press("Escape");
    await expect(workerDetail).toBeHidden();

    // The Employee role keeps no team or tenant controls: review creation
    // stays absent (self-service goal creation is a separate, allowed surface).
    await expect(worker.page.getByRole("button", { name: "New review" })).toHaveCount(0);

    // HR is not required for this ad-hoc flow, but the seeded account must
    // still be able to open the performance workspace (the tenant scope).
    const hr = await loggedIn(browser, HR_EMAIL);
    await openReviewsTab(hr.page);
    await expect(hr.page.getByRole("button", { name: "New review" })).toBeVisible();
    await expect(hr.page.getByRole("row").filter({ hasText: PERIOD })).toBeVisible();

    await hr.context.close();
    await peer.context.close();
    await worker.context.close();
    await manager.context.close();
  });
});
