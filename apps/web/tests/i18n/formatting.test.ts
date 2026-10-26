/**
 * Locale-aware formatting — the same value must render with Vietnamese month
 * names, separators and currency conventions when the active locale is `vi`,
 * while the English defaults stay unchanged for existing assertions.
 */

import { describe, expect, it } from "vitest";

import { formatPeriod, formatMoney, formatPayrollDate } from "@/features/payroll/format";
import { formatDate } from "@/features/employees/components/dates";

describe("locale-aware formatting", () => {
  it("renders month names in the active locale", () => {
    const english = formatPeriod("2026-03", "en");
    const vietnamese = formatPeriod("2026-03", "vi");

    expect(english).toContain("March");
    expect(vietnamese).not.toContain("March");
    expect(vietnamese).toContain("2026");
  });

  it("uses locale separators and currency notation", () => {
    const english = formatMoney(1234.5, "USD", "en");
    const vietnamese = formatMoney(1234.5, "USD", "vi");

    expect(english).not.toBe(vietnamese);
    expect(english).toContain("1,234.50");
    expect(vietnamese).toContain("1.234,50");
  });

  it("formats a date-only value and a timestamp consistently per locale", () => {
    expect(formatPayrollDate("2026-03-02", "vi")).not.toContain("Mar");
    expect(formatDate("2026-03-02", "vi")).not.toContain("Mar");
    expect(formatDate("2026-03-02", "en")).toContain("Mar");
  });
});
