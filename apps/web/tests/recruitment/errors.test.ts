/**
 * Recruitment label/error mapping — every pinned code has a sentence, no raw
 * code survives, and job errors land on the field that caused them.
 */

import { describe, expect, it } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import {
  candidateFieldErrors,
  hireFieldErrors,
  jobFieldErrors,
} from "@/features/recruitment/hooks/use-recruitment";
import {
  RECRUITMENT_ERROR_MESSAGES,
  isForbiddenError,
  organizationLabel,
  recruitmentErrorMessage,
} from "@/features/recruitment/labels";

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
      const message = RECRUITMENT_ERROR_MESSAGES[code];
      expect(message, code).toBeTruthy();
      expect(message, code).not.toContain(code);
      expect(recruitmentErrorMessage(new ApiError(409, code, "raw server text")), code).toBe(
        message,
      );
    }
  });

  it("falls back by status and for non-API failures", () => {
    expect(recruitmentErrorMessage(new ApiError(404, null, "gone"))).toBe(
      "This item is no longer available.",
    );
    expect(recruitmentErrorMessage(new ApiError(403, null, "denied"))).toBe(
      "You don't have permission to do that.",
    );
    expect(recruitmentErrorMessage(new Error("boom"))).toBe(
      "Something went wrong. Please try again.",
    );
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
      jobFieldErrors(new ApiError(404, "department_not_found", "Department not found")),
    ).toEqual({
      department_id: RECRUITMENT_ERROR_MESSAGES.department_not_found,
    });
    expect(jobFieldErrors(new ApiError(404, "position_not_found", "Position not found"))).toEqual({
      position_id: RECRUITMENT_ERROR_MESSAGES.position_not_found,
    });
    expect(jobFieldErrors(new ApiError(404, "location_not_found", "Location not found"))).toEqual({
      location_id: RECRUITMENT_ERROR_MESSAGES.location_not_found,
    });
  });

  it("keeps a job transition failure on the dialog root", () => {
    expect(jobFieldErrors(new ApiError(409, "job_not_open", "Job is closed"))).toEqual({
      root: RECRUITMENT_ERROR_MESSAGES.job_not_open,
    });
  });
});

describe("organizationLabel", () => {
  const names = new Map([["01900000-0000-7000-8000-000000000d01", "Engineering"]]);

  it("resolves a known reference and falls back to the id tail", () => {
    expect(organizationLabel(names, "01900000-0000-7000-8000-000000000d01", "Department")).toBe(
      "Engineering",
    );
    expect(organizationLabel(names, "01900000-0000-7000-8000-000000000d99", "Department")).toBe(
      "Department …0d99",
    );
  });
});

describe("candidate error mapping", () => {
  it("puts a duplicate application on the email field", () => {
    expect(
      candidateFieldErrors(new ApiError(409, "candidate_already_applied", "Duplicate")),
    ).toEqual({ email: RECRUITMENT_ERROR_MESSAGES.candidate_already_applied });
  });

  it("puts an intake job conflict on the job field", () => {
    expect(candidateFieldErrors(new ApiError(409, "job_not_open", "Job is closed"))).toEqual({
      job_id: RECRUITMENT_ERROR_MESSAGES.job_not_open,
    });
    expect(candidateFieldErrors(new ApiError(404, "job_not_found", "Gone"))).toEqual({
      job_id: RECRUITMENT_ERROR_MESSAGES.job_not_found,
    });
  });

  it("keeps an unknown intake failure on the dialog root", () => {
    expect(candidateFieldErrors(new ApiError(400, "unexpected", "boom"))).toEqual({
      root: "boom",
    });
  });
});

describe("hire error mapping", () => {
  it("maps the seam's email/code/manager codes to their fields", () => {
    expect(hireFieldErrors(new ApiError(409, "employee_email_taken", "Taken"))).toEqual({
      email: RECRUITMENT_ERROR_MESSAGES.employee_email_taken,
    });
    expect(hireFieldErrors(new ApiError(409, "employee_code_taken", "Taken"))).toEqual({
      employee_code: RECRUITMENT_ERROR_MESSAGES.employee_code_taken,
    });
    expect(hireFieldErrors(new ApiError(400, "employee_manager_invalid", "Invalid"))).toEqual({
      manager_id: RECRUITMENT_ERROR_MESSAGES.employee_manager_invalid,
    });
  });

  it("keeps capacity/stage failures on the dialog root", () => {
    expect(hireFieldErrors(new ApiError(409, "job_openings_filled", "Full"))).toEqual({
      root: RECRUITMENT_ERROR_MESSAGES.job_openings_filled,
    });
    expect(hireFieldErrors(new ApiError(409, "candidate_stage_conflict", "Moved"))).toEqual({
      root: RECRUITMENT_ERROR_MESSAGES.candidate_stage_conflict,
    });
  });
});
