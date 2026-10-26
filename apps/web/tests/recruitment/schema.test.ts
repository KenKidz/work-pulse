/**
 * Job form schema — the pinned title/description bounds, organization uuids
 * and the at-least-one-opening rule.
 */

import { describe, expect, it } from "vitest";

import { jobFormSchema } from "@/features/recruitment/schema";

const VALID = {
  title: "Backend Engineer",
  description: "Own the workforce API.",
  department_id: "01900000-0000-7000-8000-000000000d01",
  position_id: "01900000-0000-7000-8000-000000000201",
  location_id: "01900000-0000-7000-8000-000000000301",
  openings: 1,
};

function parse(overrides: Record<string, unknown>) {
  return jobFormSchema.safeParse({ ...VALID, ...overrides });
}

describe("jobFormSchema", () => {
  it("accepts the pinned job shape and coerces openings", () => {
    const result = jobFormSchema.safeParse({ ...VALID, openings: "3", title: "  Backend  " });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.openings).toBe(3);
      expect(result.data.title).toBe("Backend");
    }
  });

  it("rejects an empty or overlong title", () => {
    expect(parse({ title: "   " }).success).toBe(false);
    expect(parse({ title: "a".repeat(201) }).success).toBe(false);
  });

  it("rejects a description over 5,000 characters", () => {
    expect(parse({ description: "a".repeat(5001) }).success).toBe(false);
    expect(parse({ description: "a".repeat(5000) }).success).toBe(true);
  });

  it("requires organization references as uuids", () => {
    expect(parse({ department_id: "engineering" }).success).toBe(false);
    expect(parse({ position_id: "" }).success).toBe(false);
    expect(parse({ location_id: "not-a-uuid" }).success).toBe(false);
  });

  it("requires at least one whole opening", () => {
    expect(parse({ openings: 0 }).success).toBe(false);
    expect(parse({ openings: -1 }).success).toBe(false);
    expect(parse({ openings: 1.5 }).success).toBe(false);
    expect(parse({ openings: "" }).success).toBe(false);
    expect(parse({ openings: "2" }).success).toBe(true);
  });
});
