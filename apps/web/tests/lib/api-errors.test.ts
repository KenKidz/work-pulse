/**
 * Shared 429/503 copy (Phase 7, ADR 0025). The mapping is cross-surface: any
 * feature helper that consults it renders the same sentence, and a raw server
 * message never reaches the UI for either failure.
 */

import { createTranslator } from "use-intl";
import { describe, expect, it } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import {
  errorMessage as leaveErrorMessage,
  leaveFieldErrors,
} from "@/features/leave/hooks/use-leave";
import { sharedApiErrorKey, sharedApiErrorMessage, type SharedApiErrorKey } from "@/lib/api-errors";
import messages from "@/messages/en";

const common = createTranslator({ locale: "en", messages, namespace: "common.errors" });
const leave = createTranslator({ locale: "en", messages, namespace: "leave.errors" });

function t(key: SharedApiErrorKey): string {
  return common(key);
}

/** The pairing `useApiErrorTranslator` provides in components. */
function featureT(key: string): string {
  return key === "rateLimited" || key === "dependencyUnavailable"
    ? common(key)
    : leave(key as never);
}

describe("shared API error copy", () => {
  it("maps the pinned codes before status fallbacks", () => {
    expect(sharedApiErrorKey(new ApiError(429, "rate_limited", "Too many requests"))).toBe(
      "rateLimited",
    );
    expect(
      sharedApiErrorKey(new ApiError(503, "dependency_unavailable", "Service unavailable")),
    ).toBe("dependencyUnavailable");
  });

  it("falls back to the status when the body carries no code", () => {
    expect(sharedApiErrorKey(new ApiError(429, null, "Too many requests"))).toBe("rateLimited");
    expect(sharedApiErrorKey(new ApiError(503, null, "Bad gateway"))).toBe("dependencyUnavailable");
  });

  it("leaves feature-owned failures alone", () => {
    expect(sharedApiErrorKey(new ApiError(409, "employee_email_taken", "taken"))).toBeNull();
    expect(sharedApiErrorKey(new ApiError(403, "forbidden", "denied"))).toBeNull();
    expect(sharedApiErrorKey(new Error("boom"))).toBeNull();
  });

  it("resolves the pinned English sentence", () => {
    expect(sharedApiErrorMessage(new ApiError(429, "rate_limited", "raw server text"), t)).toBe(
      "You're doing that too often. Please wait a moment and try again.",
    );
    expect(
      sharedApiErrorMessage(new ApiError(503, "dependency_unavailable", "raw server text"), t),
    ).toBe("That service is temporarily unavailable. Please try again shortly.");
    expect(sharedApiErrorMessage(new ApiError(400, "validation_error", "bad"), t)).toBeNull();
  });

  it("wins over a feature helper's raw server fallback", () => {
    expect(
      leaveFieldErrors(new ApiError(429, "rate_limited", "raw server text"), featureT),
    ).toEqual({ root: t("rateLimited") });
    expect(
      leaveErrorMessage(new ApiError(503, "dependency_unavailable", "raw server text"), featureT),
    ).toBe(t("dependencyUnavailable"));
    expect(leaveFieldErrors(new ApiError(409, "leave_overlaps", "raw"), featureT)).toEqual({
      start_date: leave("overlaps"),
    });
  });
});
