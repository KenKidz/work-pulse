import { describe, expect, it } from "vitest";

import { dayjs, formatDateTime } from "@/lib/datetime";

describe("formatDateTime", () => {
  it("converts a UTC ISO timestamp into the target timezone", () => {
    expect(formatDateTime("2026-09-24T08:00:00Z", "Asia/Ho_Chi_Minh")).toBe("2026-09-24 15:00");
  });

  it("keeps the exact instant when the target timezone is UTC", () => {
    expect(formatDateTime("2026-01-15T23:05:00Z", "UTC")).toBe("2026-01-15 23:05");
  });

  it("flags unparseable input as invalid instead of throwing", () => {
    expect(formatDateTime("not-a-date", "UTC")).toBe("Invalid Date");
    expect(dayjs("not-a-date").isValid()).toBe(false);
  });
});
