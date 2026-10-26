/**
 * Recruitment label/error mapping — every pinned code has a sentence, no raw
 * code survives, and job errors land on the field that caused them.
 */

import { createTranslator } from "use-intl";
import { describe, expect, it } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import {
  candidateFieldErrors,
  hireFieldErrors,
  jobFieldErrors,
} from "@/features/recruitment/hooks/use-recruitment";
import {
  isForbiddenError,
  organizationLabel,
  recruitmentErrorMessage,
} from "@/features/recruitment/labels";
import type { RecruitmentErrorKey } from "@/features/recruitment/labels";
import messages from "@/messages/en";

const t = createTranslator({ locale: "en", messages, namespace: "recruitment.errors" });

const CODE_TO_KEY: Record<string, RecruitmentErrorKey> = {
  job_not_found: "jobNotFound",
  candidate_not_found: "candidateNotFound",
  interview_not_found: "interviewNotFound",
  interviewer_not_found: "interviewerNotFound",
  job_not_draft: "jobNotDraft",
  job_not_open: "jobNotOpen",
  job_openings_filled: "jobOpeningsFilled",
  candidate_already_applied: "candidateAlreadyApplied",
  candidate_stage_conflict: "candidateStageConflict",
  candidate_not_open: "candidateNotOpen",
  interview_not_scheduled: "interviewNotScheduled",
  interviewer_not_active: "interviewerNotActive",
  employee_email_taken: "employeeEmailTaken",
  employee_code_taken: "employeeCodeTaken",
  employee_manager_invalid: "employeeManagerInvalid",
  department_not_found: "departmentNotFound",
  position_not_found: "positionNotFound",
  location_not_found: "locationNotFound",
  recruitment_denied: "denied",
  validation_error: "validation",
  forbidden: "forbidden",
};

function messageFor(code: string): string {
  return t(CODE_TO_KEY[code] ?? "generic");
}

const PINNED_CODES = [
  "job_not_found",
  "candidate_not_found",
  "interview_not_found",
  "interviewer_not_found",
  "job_not_draft",
  "job_not_open",
  "job_openings_filled",
  "candidate_already_applied",
  "candidate_stage_conflict",
  "candidate_not_open",
  "interview_not_scheduled",
  "interviewer_not_active",
  "employee_email_taken",
  "employee_code_taken",
  "employee_manager_invalid",
  "department_not_found",
  "position_not_found",
  "location_not_found",
  "recruitment_denied",
  "validation_error",
  "forbidden",
];

describe("recruitment error messages", () => {
  it("maps every pinned code to a sentence and never echoes the code", () => {
    for (const code of PINNED_CODES) {
      const message = messageFor(code);
      expect(message, code).toBeTruthy();
      expect(message, code).not.toContain(code);
      expect(recruitmentErrorMessage(new ApiError(409, code, "raw server text"), t), code).toBe(
        message,
      );
    }
  });

  it("falls back by status and for non-API failures", () => {
    expect(recruitmentErrorMessage(new ApiError(404, null, "gone"), t)).toBe(t("notFound"));
    expect(recruitmentErrorMessage(new ApiError(403, null, "denied"), t)).toBe(
      t("forbidden"),
    );
    expect(recruitmentErrorMessage(new Error("boom"), t)).toBe(t("generic"));
  });

  it("classifies a 403 or recruitment_denied as a denied action", () => {
    expect(isForbiddenError(new ApiError(403, null, "denied"))).toBe(true);
    expect(isForbiddenError(new ApiError(409, "recruitment_denied", "denied"))).toBe(true);
    expect(isForbiddenError(new ApiError(409, "job_not_open", "conflict"))).toBe(false);
  });
});

describe("job form error mapping", () => {
  it("puts a missing organization reference on its own field", () => {
    expect(
      jobFieldErrors(
        new ApiError(404, "department_not_found", "Department not found"),
        t,
      ),
    ).toEqual({
      department_id: t("departmentNotFound"),
    });
    expect(jobFieldErrors(new ApiError(404, "position_not_found", "Position not found"), t)).toEqual({
      position_id: t("positionNotFound"),
    });
    expect(jobFieldErrors(new ApiError(404, "location_not_found", "Location not found"), t)).toEqual({
      location_id: t("locationNotFound"),
    });
  });

  it("keeps a job transition failure on the dialog root", () => {
    expect(jobFieldErrors(new ApiError(409, "job_not_open", "Job is closed"), t)).toEqual({
      root: t("jobNotOpen"),
    });
  });
});

describe("organizationLabel", () => {
  const names = new Map([["01900000-0000-7000-8000-000000000d01", "Engineering"]]);

  it("resolves a known reference and falls back to the id tail", () => {
    expect(organizationLabel(
        names,
        "01900000-0000-7000-8000-000000000d01",
        "Department …0d01",
      )).toBe(
      "Engineering",
    );
    expect(organizationLabel(
        names,
        "01900000-0000-7000-8000-000000000d99",
        "Department …0d99",
      )).toBe(
      "Department …0d99",
    );
  });
});

describe("candidate error mapping", () => {
  it("puts a duplicate application on the email field", () => {
    expect(
      candidateFieldErrors(
        new ApiError(409, "candidate_already_applied", "Duplicate"),
        t,
      ),
    ).toEqual({ email: t("candidateAlreadyApplied") });
  });

  it("puts an intake job conflict on the job field", () => {
    expect(candidateFieldErrors(new ApiError(409, "job_not_open", "Job is closed"), t)).toEqual({
      job_id: t("jobNotOpen"),
    });
    expect(candidateFieldErrors(new ApiError(404, "job_not_found", "Gone"), t)).toEqual({
      job_id: t("jobNotFound"),
    });
  });

  it("keeps an unknown intake failure on the dialog root", () => {
    expect(candidateFieldErrors(new ApiError(400, "unexpected", "boom"), t)).toEqual({
      root: "boom",
    });
  });
});

describe("hire error mapping", () => {
  it("maps the seam's email/code/manager codes to their fields", () => {
    expect(hireFieldErrors(new ApiError(409, "employee_email_taken", "Taken"), t)).toEqual({
      email: t("employeeEmailTaken"),
    });
    expect(hireFieldErrors(new ApiError(409, "employee_code_taken", "Taken"), t)).toEqual({
      employee_code: t("employeeCodeTaken"),
    });
    expect(hireFieldErrors(new ApiError(400, "employee_manager_invalid", "Invalid"), t)).toEqual({
      manager_id: t("employeeManagerInvalid"),
    });
  });

  it("keeps capacity/stage failures on the dialog root", () => {
    expect(hireFieldErrors(new ApiError(409, "job_openings_filled", "Full"), t)).toEqual({
      root: t("jobOpeningsFilled"),
    });
    expect(hireFieldErrors(new ApiError(409, "candidate_stage_conflict", "Moved"), t)).toEqual({
      root: t("candidateStageConflict"),
    });
  });
});
