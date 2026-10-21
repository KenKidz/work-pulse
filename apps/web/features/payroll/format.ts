/**
 * Display formatting for payroll data. Amounts leave the API as JSON numbers
 * with 2dp (ADR 0017); the tenant's single currency decides the symbol and
 * grouping. Dates and timestamps come from `lib/datetime` (dayjs), never from
 * ad-hoc `new Date(...)` arithmetic.
 */

import { dayjs } from "@/lib/datetime";

/** A `YYYY-MM` period as its readable month. */
export function formatPeriod(period: string): string {
  return dayjs(`${period}-01`).format("MMMM YYYY");
}

/** An API date (`YYYY-MM-DD`) in the browser's local zone. */
export function formatPayrollDate(value: string): string {
  return dayjs(value).format("MMM D, YYYY");
}

/** A UTC ISO timestamp in the browser's local zone. */
export function formatPayrollTimestamp(value: string): string {
  return dayjs(value).format("MMM D, YYYY HH:mm");
}

/** The current UTC date as the API's `YYYY-MM-DD` string. */
export function todayISO(): string {
  return dayjs.utc().format("YYYY-MM-DD");
}

/** The last closed month — a run may only cover a month that has ended. */
export function lastClosedPeriod(): string {
  return dayjs.utc().subtract(1, "month").format("YYYY-MM");
}

/**
 * Money with exactly two decimals. The API pins ISO-4217-shaped codes, but
 * `Intl` only knows registered currencies — fall back to `CODE 1,234.56`
 * rather than throwing on an unrecognised code. Formatters are cached by
 * currency because a payslip table can format hundreds of cells per render.
 */
const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string): Intl.NumberFormat | null {
  const cached = moneyFormatters.get(currency);
  if (cached !== undefined) return cached;

  try {
    const formatter = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    moneyFormatters.set(currency, formatter);
    return formatter;
  } catch {
    return null;
  }
}

export function formatMoney(amount: number, currency: string): string {
  const formatter = moneyFormatter(currency);
  return formatter === null ? `${currency} ${amount.toFixed(2)}` : formatter.format(amount);
}

const hoursFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/** Hours/quantities with up to two decimals; never padded with zeros. */
export function formatHours(value: number): string {
  return hoursFormatter.format(value);
}

/**
 * A joined employee name when the directory is readable for this role, else
 * the app-wide id-tail convention (`Employee …a1b2`) — the same fallback the
 * users table uses for unlinked rows.
 */
export function employeeLabel(employeeId: string, directory: ReadonlyMap<string, string>): string {
  const name = directory.get(employeeId);
  return name !== undefined && name.length > 0 ? name : `Employee …${employeeId.slice(-4)}`;
}
