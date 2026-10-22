/**
 * Overtime form schemas (T7) — mirror the pinned API contract so obvious input
 * errors never reach the server: a real calendar date, hours > 0 and ≤ 12 with
 * at most two decimals, and 500-character reason/note bounds.
 */

import { describe, expect, it } from "vitest";

import { overtimeDecisionFormSchema, overtimeRequestFormSchema } from "@/features/overtime/schema";

const BASE = {
  work_date: "2026-10-01",
  hours: "2.5",
  reason: "Quarter close",
};

function parse(overrides: Record<string, string> = {}) {
  return overtimeRequestFormSchema.safeParse({ ...BASE, ...overrides });
}

describe("overtimeRequestFormSchema", () => {
  it("accepts a calendar work date and hours up to two decimals", () => {
    expect(parse().success).toBe(true);
    expect(parse({ hours: "12" }).success).toBe(true);
    expect(parse({ hours: "0.01" }).success).toBe(true);
    expect(parse({ hours: "12.00" }).success).toBe(true);
  });

  it("rejects a non-calendar or malformed work date", () => {
    expect(parse({ work_date: "2026-02-30" }).success).toBe(false);
    expect(parse({ work_date: "2026-13-01" }).success).toBe(false);
    expect(parse({ work_date: "01/10/2026" }).success).toBe(false);
    expect(parse({ work_date: "" }).success).toBe(false);
  });

  it("rejects hours outside 0 < hours ≤ 12", () => {
    expect(parse({ hours: "0" }).success).toBe(false);
    expect(parse({ hours: "-1" }).success).toBe(false);
    expect(parse({ hours: "12.01" }).success).toBe(false);
    expect(parse({ hours: "99" }).success).toBe(false);
  });

  it("rejects more than two decimal places", () => {
    expect(parse({ hours: "1.234" }).success).toBe(false);
    expect(parse({ hours: "0.001" }).success).toBe(false);
  });

  it("accepts an omitted or empty reason but bounds a present one", () => {
    expect(
      overtimeRequestFormSchema.safeParse({
        work_date: BASE.work_date,
        hours: BASE.hours,
      }).success,
    ).toBe(true);
    expect(parse({ reason: "" }).success).toBe(true);
    expect(parse({ reason: "x".repeat(500) }).success).toBe(true);
    expect(parse({ reason: "x".repeat(501) }).success).toBe(false);
  });
});

describe("overtimeDecisionFormSchema", () => {
  it("accepts an omitted or bounded note and rejects an oversized one", () => {
    expect(overtimeDecisionFormSchema.safeParse({}).success).toBe(true);
    expect(overtimeDecisionFormSchema.safeParse({ note: "x".repeat(500) }).success).toBe(true);
    expect(overtimeDecisionFormSchema.safeParse({ note: "x".repeat(501) }).success).toBe(false);
  });
});
