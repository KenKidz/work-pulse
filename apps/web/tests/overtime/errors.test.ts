/**
 * Overtime error-code mapping (T7). Every pinned submit/decision/withdraw code
 * must render a sentence — never the raw code — and field-level refusals must
 * land on the field that caused them.
 */

import { describe, expect, it } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import {
  OVERTIME_ERROR_MESSAGES,
  overtimeDecisionError,
  overtimeFieldErrors,
  overtimeWithdrawError,
} from "@/features/overtime/hooks/use-overtime";

const PINNED_CODES = [
  "overtime_overlaps",
  "overtime_work_date_in_future",
  "overtime_not_requested",
  "overtime_not_manager_approved",
  "overtime_not_open",
  "employee_not_active",
  "overtime_request_not_found",
  "employee_not_found",
] as const;

function apiError(status: number, code: string | null, message = "server text"): ApiError {
  return new ApiError(status, code, message);
}

describe("OVERTIME_ERROR_MESSAGES", () => {
  it.each(PINNED_CODES)("maps %s to a sentence without the raw code", (code) => {
    const message = OVERTIME_ERROR_MESSAGES[code];

    expect(message).toBeDefined();
    expect(message).not.toContain(code);
    expect(message?.length ?? 0).toBeGreaterThan(0);
  });

  it("maps a forbidden response and a missing request to readable sentences", () => {
    expect(overtimeDecisionError(apiError(403, null, "Forbidden"))).toBe(
      OVERTIME_ERROR_MESSAGES.forbidden,
    );
    expect(overtimeWithdrawError(apiError(404, null, "Not found"))).toBe(
      OVERTIME_ERROR_MESSAGES.overtime_request_not_found,
    );
  });

  it("falls back to the server message for an unmapped code", () => {
    expect(overtimeDecisionError(apiError(400, "something_else", "A readable message"))).toBe(
      "A readable message",
    );
  });

  it("returns the generic sentence for a non-API failure", () => {
    expect(overtimeFieldErrors(new Error("boom")).root).toBe(
      "Something went wrong. Please try again.",
    );
  });
});

describe("field-level error mapping", () => {
  it("puts overlap and future-date refusals on the work-date field", () => {
    expect(overtimeFieldErrors(apiError(409, "overtime_overlaps")).work_date).toBe(
      "You already have an open overtime request for that date.",
    );
    expect(overtimeFieldErrors(apiError(409, "overtime_work_date_in_future")).work_date).toContain(
      "claimed for hours already worked",
    );
  });

  it("puts inactive/missing employee failures on the dialog root", () => {
    expect(overtimeFieldErrors(apiError(409, "employee_not_active")).root).toContain(
      "isn’t active",
    );
    expect(overtimeFieldErrors(apiError(404, "employee_not_found")).root).toContain(
      "No employee record",
    );
  });
});

describe("decision and withdraw refusals", () => {
  it("renders a 403 in a decision dialog as the self-decision refusal", () => {
    expect(overtimeDecisionError(apiError(403, "forbidden"))).toBe(
      "You can’t decide your own overtime request.",
    );
  });

  it("maps every wrong-state 409 to its pinned sentence", () => {
    expect(overtimeDecisionError(apiError(409, "overtime_not_requested"))).toBe(
      OVERTIME_ERROR_MESSAGES.overtime_not_requested,
    );
    expect(overtimeDecisionError(apiError(409, "overtime_not_manager_approved"))).toBe(
      OVERTIME_ERROR_MESSAGES.overtime_not_manager_approved,
    );
    expect(overtimeDecisionError(apiError(409, "overtime_not_open"))).toBe(
      OVERTIME_ERROR_MESSAGES.overtime_not_open,
    );
  });

  it("renders a 403 on withdraw as the own-only refusal", () => {
    expect(overtimeWithdrawError(apiError(403, "forbidden"))).toBe(
      "You can only withdraw your own overtime request.",
    );
  });
});
