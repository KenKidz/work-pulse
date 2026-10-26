/**
 * Locale-aware display formatting for payroll data. Amounts leave the API as
 * JSON numbers with 2dp (ADR 0017); the tenant's single currency decides the
 * symbol and grouping while the active locale decides separators and month
 * names. Dates use `Intl` directly so SSR and client agree.
 */

import { dayjs } from "@/lib/datetime";

/** A `YYYY-MM` period as its readable month in the active locale. */
export function formatPeriod(period: string, locale: string): string {
  const year = Number(period.slice(0, 4));
  const month = Number(period.slice(5, 7));
  return new Intl.DateTimeFormat(locale, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, 1)));
}

/** An API date (`YYYY-MM-DD`) in the active locale. */
export function formatPayrollDate(value: string, locale: string): string {
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(dateOnly ? `${value}T00:00:00` : value));
}

/** A UTC ISO timestamp in the active locale and the browser's local zone. */
export function formatPayrollTimestamp(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
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
 * Money with exactly two decimals. `Intl` only knows registered currencies —
 * fall back to `CODE 1,234.56` rather than throwing on an unrecognised code.
 * Formatters are cached by locale + currency because payslip tables format
 * hundreds of cells per render.
 */
const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string, locale: string): Intl.NumberFormat | null {
  const key = `${locale}:${currency}`;
  const cached = moneyFormatters.get(key);
  if (cached !== undefined) return cached;

  try {
    const formatter = new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    moneyFormatters.set(key, formatter);
    return formatter;
  } catch {
    return null;
  }
}

export function formatMoney(amount: number, currency: string, locale: string): string {
  const formatter = moneyFormatter(currency, locale);
  return formatter === null ? `${currency} ${amount.toFixed(2)}` : formatter.format(amount);
}

const amountFormatters = new Map<string, Intl.NumberFormat>();

function amountFormatter(locale: string): Intl.NumberFormat {
  const cached = amountFormatters.get(locale);
  if (cached !== undefined) return cached;
  const formatter = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  amountFormatters.set(locale, formatter);
  return formatter;
}

/**
 * An amount without a currency symbol, for self-service payslips. Employees
 * cannot read the tenant config, so "My payslips" states the currency in copy.
 */
export function formatAmount(amount: number, locale: string): string {
  return amountFormatter(locale).format(amount);
}

const hoursFormatters = new Map<string, Intl.NumberFormat>();

/** Hours/quantities with up to two decimals; never padded with zeros. */
export function formatHours(value: number, locale: string): string {
  const cached = hoursFormatters.get(locale);
  if (cached !== undefined) return cached.format(value);
  const formatter = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
  hoursFormatters.set(locale, formatter);
  return formatter.format(value);
}

/**
 * A joined employee name when the directory is readable for this role, else
 * the localized id-tail fallback (`Employee …a1b2`).
 */
export function employeeLabel(
  employeeId: string,
  directory: ReadonlyMap<string, string>,
  fallback: string,
): string {
  const name = directory.get(employeeId);
  return name !== undefined && name.length > 0 ? name : fallback;
}
