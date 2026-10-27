/**
 * Performance form schemas — the pinned goal bounds.
 */

import { describe, expect, it } from "vitest";

import { goalFormSchema, SELF_EMPLOYEE } from "@/features/performance/schema";

const t = (key: string) => key;

const VALID = {
  employee_id: SELF_EMPLOYEE,
  title: "Ship the onboarding revamp",
  description: "Rewrite the first-week checklist.",
  due_date: "2026-12-31",
  progress_pct: 20,
};

function parse(overrides: Record<string, unknown>) {
  return goalFormSchema(t).safeParse({ ...VALID, ...overrides });
}

describe("goalFormSchema", () => {
  it("accepts the pinned shape and trims text", () => {
    const result = goalFormSchema(t).safeParse({
      ...VALID,
      title: "  Ship the revamp ",
      description: "  First week only. ",
      progress_pct: "40",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.title).toBe("Ship the revamp");
      expect(result.data.description).toBe("First week only.");
      expect(result.data.progress_pct).toBe(40);
    }
  });

  it("accepts the self sentinel and a real employee uuid", () => {
    expect(parse({ employee_id: SELF_EMPLOYEE }).success).toBe(true);
    expect(parse({ employee_id: "01900000-0000-7000-8000-0000000000a2" }).success).toBe(true);
    expect(parse({ employee_id: "" }).success).toBe(false);
    expect(parse({ employee_id: "wendy" }).success).toBe(false);
  });

  it("rejects an empty or overlong title", () => {
    expect(parse({ title: "   " }).success).toBe(false);
    expect(parse({ title: "a".repeat(201) }).success).toBe(false);
  });

  it("caps the description at 2,000 characters", () => {
    expect(parse({ description: "a".repeat(2000) }).success).toBe(true);
    expect(parse({ description: "a".repeat(2001) }).success).toBe(false);
  });

  it("accepts an empty or past due date and rejects an impossible one", () => {
    expect(parse({ due_date: "" }).success).toBe(true);
    expect(parse({ due_date: "2020-02-29" }).success).toBe(true);
    expect(parse({ due_date: "2021-02-29" }).success).toBe(false);
    expect(parse({ due_date: "2026-13-01" }).success).toBe(false);
    expect(parse({ due_date: "next week" }).success).toBe(false);
  });

  it("requires whole progress between 0 and 100", () => {
    expect(parse({ progress_pct: 0 }).success).toBe(true);
    expect(parse({ progress_pct: 100 }).success).toBe(true);
    expect(parse({ progress_pct: -1 }).success).toBe(false);
    expect(parse({ progress_pct: 101 }).success).toBe(false);
    expect(parse({ progress_pct: 1.5 }).success).toBe(false);
    expect(parse({ progress_pct: "abc" }).success).toBe(false);
  });
});
