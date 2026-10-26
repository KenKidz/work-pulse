/**
 * Recruitment form schemas — the pinned job, candidate and hire bounds.
 */

import { describe, expect, it } from "vitest";

import { dayjs } from "@/lib/datetime";

import { todayISO } from "@/features/recruitment/dates";
import {
  candidateCreateSchema,
  candidateDecisionSchema,
  candidateFormSchema,
  hireFormSchema,
  jobFormSchema,
} from "@/features/recruitment/schema";

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

const VALID_CANDIDATE = {
  first_name: "Amara",
  last_name: "Okafor",
  email: "amara.okafor@example.test",
  phone: "+84 90 123 4567",
  source: "referral",
  notes: "Referred by the platform team.",
} as const;

function parseCandidate(overrides: Record<string, unknown>) {
  return candidateFormSchema.safeParse({ ...VALID_CANDIDATE, ...overrides });
}

describe("candidateFormSchema", () => {
  it("accepts the pinned contact shape and trims text", () => {
    const result = candidateFormSchema.safeParse({
      ...VALID_CANDIDATE,
      first_name: "  Amara ",
      notes: "",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.first_name).toBe("Amara");
      expect(result.data.notes).toBe("");
    }
  });

  it("rejects empty names, invalid emails and overlong values", () => {
    expect(parseCandidate({ first_name: "   " }).success).toBe(false);
    expect(parseCandidate({ last_name: "" }).success).toBe(false);
    expect(parseCandidate({ email: "not-an-email" }).success).toBe(false);
    expect(parseCandidate({ email: "a".repeat(321) }).success).toBe(false);
    expect(parseCandidate({ phone: "1".repeat(51) }).success).toBe(false);
    expect(parseCandidate({ notes: "a".repeat(2001) }).success).toBe(false);
    expect(parseCandidate({ source: "careers" }).success).toBe(false);
  });
});

describe("candidateCreateSchema", () => {
  it("requires the application's job as a uuid", () => {
    expect(
      candidateCreateSchema.safeParse({
        ...VALID_CANDIDATE,
        job_id: "01900000-0000-7000-8000-0000000000b1",
      }).success,
    ).toBe(true);
    expect(candidateCreateSchema.safeParse({ ...VALID_CANDIDATE, job_id: "" }).success).toBe(false);
    expect(candidateCreateSchema.safeParse({ ...VALID_CANDIDATE, job_id: "backend" }).success).toBe(
      false,
    );
  });
});

describe("candidateDecisionSchema", () => {
  it("allows an empty note and caps it at 500 characters", () => {
    expect(candidateDecisionSchema.safeParse({ note: "" }).success).toBe(true);
    expect(candidateDecisionSchema.safeParse({ note: "a".repeat(500) }).success).toBe(true);
    expect(candidateDecisionSchema.safeParse({ note: "a".repeat(501) }).success).toBe(false);
  });
});

const VALID_HIRE = {
  employment_type: "full_time",
  hired_at: "",
  manager_id: "none",
  employee_code: "",
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
} as const;

function parseHire(overrides: Record<string, unknown>) {
  return hireFormSchema.safeParse({ ...VALID_HIRE, ...overrides });
}

describe("hireFormSchema", () => {
  it("accepts the minimal hire terms with blank overrides", () => {
    const result = parseHire({ hired_at: todayISO() });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.hired_at).toBe(todayISO());
      expect(result.data.employment_type).toBe("full_time");
    }
  });

  it("rejects a hire date in the future or an impossible calendar date", () => {
    const tomorrow = dayjs.utc().add(1, "day").format("YYYY-MM-DD");
    expect(parseHire({ hired_at: tomorrow }).success).toBe(false);
    // 2021 is not a leap year; 2020 is. Both are safely in the past, so the
    // assertions stay true regardless of when the gate runs.
    expect(parseHire({ hired_at: "2021-02-29" }).success).toBe(false);
    expect(parseHire({ hired_at: "2020-02-29" }).success).toBe(true);
    expect(parseHire({ hired_at: "2026-13-01" }).success).toBe(false);
    expect(parseHire({ hired_at: "" }).success).toBe(false);
  });

  it("accepts an optional valid email override and rejects an invalid one", () => {
    expect(parseHire({ hired_at: todayISO(), email: "" }).success).toBe(true);
    expect(parseHire({ hired_at: todayISO(), email: "amara.okafor@workpulse.test" }).success).toBe(
      true,
    );
    expect(parseHire({ hired_at: todayISO(), email: "not-an-email" }).success).toBe(false);
    expect(parseHire({ hired_at: todayISO(), employee_code: "a".repeat(51) }).success).toBe(false);
  });

  it("rejects an unknown employment type", () => {
    expect(parseHire({ hired_at: todayISO(), employment_type: "seasonal" }).success).toBe(false);
  });
});
