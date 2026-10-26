/**
 * Recruitment e2e — the cross-surface promise of the feature (T9):
 *
 * 1. HR creates a job through `/recruitment`, opens it, adds a candidate and
 *    advances it to `interviewing`. Re-adding the same email to the same job is
 *    refused with the pinned `409 candidate_already_applied` message.
 * 2. HR schedules an interview for the seeded manager. The manager opens
 *    "My interviews" in a second browser context, files `rating=4` and the row
 *    flips to `completed` without a reload.
 * 3. HR advances the candidate to `offered`, hires it (the Employees seam
 *    creates the employee), and the new employee is visible in Employees.
 * 4. An Employee session never sees the Recruitment nav and a direct
 *    `/recruitment` URL redirects to the dashboard.
 *
 * Jobs/candidates/interviews and the employees a hire creates are mutable
 * through the UI, so `full-hop.sh` resets the seeded tenant's recruitment
 * fixtures before Playwright starts, and this spec repeats that reset so a
 * Playwright retry starts clean against the same dev database
 * (`.ai/testing.md` rule 6 — synthetic data only).
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

import { HR_EMAIL, MANAGER_EMAIL, WORKER_EMAIL, uiLogin } from "./auth";

const RUN = Date.now().toString(36);
const JOB_TITLE = `E2E Backend Engineer ${RUN}`;
const FIRST_NAME = "Nadia";
const LAST_NAME = `Hire-${RUN}`;
const FULL_NAME = `${FIRST_NAME} ${LAST_NAME}`;
const EMAIL = `e2e.recruitment.${RUN}@workpulse.test`;

// Seed organizations and the seeded manager interviewer.
const DEPARTMENT = "Backend";
const POSITION = "Software Engineer";
const LOCATION = "Ho Chi Minh Office";
const MANAGER_NAME = "Morgan Manager";

const API_DIR = join(process.cwd(), "apps/api");
const RESET_SCRIPT = join(API_DIR, "scripts", "reset_recruitment_fixtures.py");
const API_PYTHON = join(API_DIR, ".venv", "bin", "python");

/** Port-agnostic path comparison for the API responses this spec waits on. */
function apiPath(url: string): string {
  return new URL(url).pathname;
}

/** The server's UTC hire date (the form's default and max). */
function utcToday(): string {
  return new Date().toISOString().slice(0, 10);
}

/** A `datetime-local` value in the browser's local zone, a few days out. */
function localDateTime(offsetDays: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  date.setHours(10, 0, 0, 0);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

/**
 * A retry starts from a clean recruitment slate. `full-hop.sh` resets once
 * before Playwright, but a CI retry re-enters this test without rerunning the
 * hop.
 */
function resetRecruitmentFixtures(): void {
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

async function fillCandidate(
  page: Page,
  dialog: Locator,
  jobTitle: string,
  lastName: string,
) {
  await selectOption(page, dialog, "Job", jobTitle);
  await dialog.getByLabel("First name").fill(FIRST_NAME);
  await dialog.getByLabel("Last name").fill(lastName);
  await dialog.getByLabel("Email").fill(EMAIL);
  await dialog.getByLabel("Phone (optional)").fill("+84 90 555 0101");
  await dialog.getByLabel("Notes (optional)").fill("E2E recruitment candidate.");
}

test.describe.serial("recruitment", () => {
  test("job → candidate → interview → feedback → hire, with the pinned refusals", async ({
    browser,
  }) => {
    resetRecruitmentFixtures();

    const hr = await loggedIn(browser, HR_EMAIL);
    const interviewAt = localDateTime(2);

    // --- HR: create a draft job and open it ------------------------------
    await hr.page.goto("/recruitment");
    await expect(
      hr.page.getByRole("heading", { name: "Recruitment", exact: true }),
    ).toBeVisible();
    await expect(hr.page.getByRole("tab", { name: "Jobs", exact: true })).toBeVisible();

    await hr.page.getByRole("button", { name: "New job" }).click();
    const jobDialog = hr.page.getByRole("dialog");
    await expect(jobDialog.getByRole("heading", { name: "New job" })).toBeVisible();
    await jobDialog.getByLabel("Title").fill(JOB_TITLE);
    await jobDialog.getByLabel("Openings").fill("1");
    await selectOption(hr.page, jobDialog, "Department", DEPARTMENT);
    await selectOption(hr.page, jobDialog, "Position", POSITION);
    await selectOption(hr.page, jobDialog, "Location", LOCATION);

    const [createdJobResponse] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) === "/api/v1/recruitment/jobs",
      ),
      jobDialog.getByRole("button", { name: "Create job" }).click(),
    ]);
    expect(createdJobResponse.status(), await createdJobResponse.text()).toBe(201);
    const job = (await createdJobResponse.json()) as {
      id: string;
      title: string;
      status: string;
    };
    expect(job.status).toBe("draft");
    expect(job.title).toBe(JOB_TITLE);
    await expect(jobDialog).toBeHidden();

    const jobRow = hr.page.getByRole("row").filter({ hasText: JOB_TITLE });
    await expect(jobRow.locator("[data-status='draft']")).toBeVisible();
    await jobRow.getByRole("button", { name: `Open ${JOB_TITLE}` }).click();

    const openDialog = hr.page.getByRole("dialog");
    await expect(openDialog.getByRole("heading", { name: "Open job" })).toBeVisible();
    const [opened] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) === `/api/v1/recruitment/jobs/${job.id}/open`,
      ),
      openDialog.getByRole("button", { name: "Open job" }).click(),
    ]);
    expect(opened.status(), await opened.text()).toBe(200);
    await expect(openDialog).toBeHidden();
    await expect(jobRow.locator("[data-status='open']")).toBeVisible();

    // --- HR: add a candidate and move it to interviewing -----------------
    await hr.page.getByRole("tab", { name: "Candidates", exact: true }).click();
    await hr.page.getByRole("button", { name: "New candidate" }).click();
    let candidateDialog = hr.page.getByRole("dialog");
    await expect(candidateDialog.getByRole("heading", { name: "New candidate" })).toBeVisible();
    await fillCandidate(hr.page, candidateDialog, JOB_TITLE, LAST_NAME);

    const [candidateResponse] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) === `/api/v1/recruitment/jobs/${job.id}/candidates`,
      ),
      candidateDialog.getByRole("button", { name: "Add candidate" }).click(),
    ]);
    expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
    const candidate = (await candidateResponse.json()) as { id: string; status: string };
    expect(candidate.status).toBe("applied");
    await expect(candidateDialog).toBeHidden();

    const candidateRow = hr.page.getByRole("row").filter({ hasText: EMAIL });
    await expect(candidateRow).toContainText(FULL_NAME);
    await expect(candidateRow.locator("[data-status='applied']")).toBeVisible();

    // --- Failure case: the same email re-applied to the same job ---------
    await hr.page.getByRole("button", { name: "New candidate" }).click();
    candidateDialog = hr.page.getByRole("dialog");
    await fillCandidate(hr.page, candidateDialog, JOB_TITLE, `Duplicate-${RUN}`);
    const [duplicate] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) === `/api/v1/recruitment/jobs/${job.id}/candidates`,
      ),
      candidateDialog.getByRole("button", { name: "Add candidate" }).click(),
    ]);
    expect(duplicate.status(), await duplicate.text()).toBe(409);
    await expect(
      candidateDialog.getByText("This candidate has already applied to this job."),
    ).toBeVisible();
    await candidateDialog.getByRole("button", { name: "Cancel" }).click();
    await expect(candidateDialog).toBeHidden();

    await candidateRow
      .getByRole("button", { name: `Advance ${FULL_NAME}` })
      .click();
    await expect(candidateRow.locator("[data-status='interviewing']")).toBeVisible();

    // --- HR: schedule the interview for the seeded manager ---------------
    await hr.page.getByRole("tab", { name: "Interviews", exact: true }).click();
    await hr.page.getByRole("button", { name: "Schedule interview" }).click();
    const scheduleDialog = hr.page.getByRole("dialog");
    await expect(
      scheduleDialog.getByRole("heading", { name: "Schedule interview" }),
    ).toBeVisible();
    await selectOption(hr.page, scheduleDialog, "Candidate", FULL_NAME);
    await selectOption(hr.page, scheduleDialog, "Interviewer", MANAGER_NAME);
    await scheduleDialog.getByLabel("Scheduled at").fill(interviewAt);
    await scheduleDialog.getByLabel("Notes (optional)").fill("E2E first round.");

    const [interviewResponse] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()) ===
            `/api/v1/recruitment/candidates/${candidate.id}/interviews`,
      ),
      scheduleDialog.getByRole("button", { name: "Schedule interview" }).click(),
    ]);
    expect(interviewResponse.status(), await interviewResponse.text()).toBe(201);
    expect(((await interviewResponse.json()) as { status: string }).status).toBe("scheduled");
    await expect(scheduleDialog).toBeHidden();

    const interviewRow = hr.page
      .getByTestId("interview-row")
      .filter({ hasText: FULL_NAME });
    await expect(interviewRow.locator("[data-status='scheduled']")).toBeVisible();

    // --- The manager files feedback from "My interviews" -----------------
    const manager = await loggedIn(browser, MANAGER_EMAIL);
    await manager.page.goto("/recruitment");
    await expect(manager.page.getByRole("tab", { name: "My interviews", exact: true })).toBeVisible();
    const myRow = manager.page
      .getByTestId("my-interview-row")
      .filter({ hasText: FULL_NAME });
    await expect(myRow).toHaveAttribute("data-status", "scheduled");

    await myRow.getByRole("button", { name: `Submit feedback for ${FULL_NAME}` }).click();
    const feedbackDialog = manager.page.getByRole("dialog");
    await expect(
      feedbackDialog.getByRole("heading", { name: "Submit feedback" }),
    ).toBeVisible();
    await selectOption(manager.page, feedbackDialog, "Rating", "4 — Strong");
    await feedbackDialog
      .getByLabel("Feedback notes (optional)")
      .fill("Strong systems thinking; follow up on scale.");

    const [completed] = await Promise.all([
      manager.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()).endsWith("/complete"),
      ),
      feedbackDialog.getByRole("button", { name: "Submit feedback" }).click(),
    ]);
    expect(completed.status(), await completed.text()).toBe(200);
    expect(((await completed.json()) as { status: string }).status).toBe("completed");
    await expect(feedbackDialog).toBeHidden();
    // No reload: the mutation invalidates the list and the row flips.
    await expect(myRow).toHaveAttribute("data-status", "completed");
    await expect(myRow).toContainText("4 / 5");

    // --- HR: offered → hired through the seam ----------------------------
    await hr.page.reload();
    await hr.page.getByRole("tab", { name: "Interviews", exact: true }).click();
    await expect(
      hr.page
        .getByTestId("interview-row")
        .filter({ hasText: FULL_NAME })
        .locator("[data-status='completed']"),
    ).toBeVisible();

    await hr.page.getByRole("tab", { name: "Candidates", exact: true }).click();
    await candidateRow
      .getByRole("button", { name: `Advance ${FULL_NAME}` })
      .click();
    await expect(candidateRow.locator("[data-status='offered']")).toBeVisible();

    await candidateRow.getByRole("button", { name: `Hire ${FULL_NAME}` }).click();
    const hireDialog = hr.page.getByRole("dialog");
    await expect(hireDialog.getByRole("heading", { name: "Hire candidate" })).toBeVisible();
    await expect(hireDialog.getByLabel("Hire date")).toHaveValue(utcToday());
    await selectOption(hr.page, hireDialog, "Manager (optional)", MANAGER_NAME);

    const [hired] = await Promise.all([
      hr.page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          apiPath(response.url()).endsWith("/hire"),
      ),
      hireDialog.getByRole("button", { name: "Hire candidate" }).click(),
    ]);
    expect(hired.status(), await hired.text()).toBe(200);
    const hiredBody = (await hired.json()) as {
      status: string;
      employee_id: string | null;
    };
    expect(hiredBody.status).toBe("hired");
    expect(hiredBody.employee_id).toBeTruthy();
    await expect(hireDialog).toBeHidden();
    await expect(candidateRow.locator("[data-status='hired']")).toBeVisible();

    // The new employee exists and is visible in the Employees workspace.
    await candidateRow.getByRole("link", { name: `View employee ${FULL_NAME}` }).click();
    await expect(hr.page).toHaveURL(/\/employees\/[0-9a-f-]{36}$/);
    await expect(hr.page.getByRole("heading", { name: FULL_NAME })).toBeVisible();

    await hr.page.goto("/employees");
    await hr.page.getByLabel("Search employees").fill(EMAIL);
    const employeeRow = hr.page.getByRole("row").filter({ hasText: EMAIL });
    await expect(employeeRow).toContainText(FULL_NAME);

    // --- Denied surface: an Employee never sees Recruitment --------------
    const worker = await loggedIn(browser, WORKER_EMAIL);
    await expect(worker.page.getByRole("link", { name: "Recruitment" })).toHaveCount(0);

    await worker.page.goto("/recruitment");
    await expect(worker.page).toHaveURL(/\/dashboard$/);
    await expect(worker.page.getByTestId("recruitment-workspace")).toHaveCount(0);
    await expect(worker.page.getByRole("tab", { name: "My interviews", exact: true })).toHaveCount(0);

    await worker.context.close();
    await manager.context.close();
    await hr.context.close();
  });
});
