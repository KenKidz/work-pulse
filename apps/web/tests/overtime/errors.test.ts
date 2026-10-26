/**
 * Overtime error-code mapping (T7). Every pinned submit/decision/withdraw code
 * must render a sentence — never the raw code — and field-level refusals must
 * land on the field that caused them.
 */

import { createTranslator } from "use-intl";
import { describe, expect, it } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import {
  overtimeDecisionError,
  overtimeFieldErrors,
  overtimeWithdrawError,
} from "@/features/overtime/hooks/use-overtime";
import messages from "@/messages/en";

const t = createTranslator({ locale: "en", messages, namespace: "overtime.errors" });

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

describe("overtime error catalog", () => {
  it.each(PINNED_CODES)("maps %s to a sentence without the raw code", (code) => {
    const mapped =
      overtimeFieldErrors(apiError(409, code), t).root ??
      overtimeFieldErrors(apiError(409, code), t).work_date ??
      overtimeDecisionError(apiError(409, code), t);
    expect(mapped).toBeTruthy();
    expect(mapped).not.toContain(code);
  });

  it("maps a forbidden response and a missing request to readable sentences", () => {
    expect(overtimeDecisionError(apiError(403, null, "Forbidden"), t)).toBe(t("selfDecision"));
    expect(overtimeWithdrawError(apiError(404, null, "Not found"), t)).toBe(t("requestNotFound"));
  });

  it("falls back to the server message for an unmapped code", () => {
    expect(overtimeDecisionError(apiError(400, "something_else", "A readable message"), t)).toBe(
      "A readable message",
    );
  });

  it("returns the generic sentence for a non-API failure", () => {
    expect(overtimeFieldErrors(new Error("boom"), t).root).toBe(t("generic"));
  });
});

describe("field-level error mapping", () => {
  it("puts overlap and future-date refusals on the work-date field", () => {
    expect(overtimeFieldErrors(apiError(409, "overtime_overlaps"), t).work_date).toBe(
      t("overlaps"),
    );
    expect(
      overtimeFieldErrors(apiError(409, "overtime_work_date_in_future"), t).work_date,
    ).toBe(t("futureDate"));
  });

  it("puts inactive/missing employee failures on the dialog root", () => {
    expect(overtimeFieldErrors(apiError(409, "employee_not_active"), t).root).toBe(
      t("employeeNotActive"),
    );
    expect(overtimeFieldErrors(apiError(404, "employee_not_found"), t).root).toBe(
      t("employeeNotFound"),
    );
  });
});

describe("decision and withdraw refusals", () => {
  it("renders a 403 in a decision dialog as the self-decision refusal", () => {
    expect(overtimeDecisionError(apiError(403, "forbidden"), t)).toBe(t("selfDecision"));
  });

  it("maps every wrong-state 409 to its pinned sentence", () => {
    expect(overtimeDecisionError(apiError(409, "overtime_not_requested"), t)).toBe(
      t("notRequested"),
    );
    expect(overtimeDecisionError(apiError(409, "overtime_not_manager_approved"), t)).toBe(
      t("notManagerApproved"),
    );
    expect(overtimeDecisionError(apiError(409, "overtime_not_open"), t)).toBe(t("notOpen"));
  });

  it("renders a 403 on withdraw as the own-only refusal", () => {
    expect(overtimeWithdrawError(apiError(403, "forbidden"), t)).toBe(t("forbiddenWithdraw"));
  });
});
