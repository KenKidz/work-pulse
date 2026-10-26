/**
 * Locale plumbing shared by the server request config, the switcher and the
 * cookie action. URLs stay locale-agnostic: the active locale lives in the
 * `wp_locale` cookie (ADR-free web decision recorded in `.ai/features/i18n.md`).
 */

export const LOCALES = ["en", "vi"] as const;

export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";

export const LOCALE_COOKIE = "wp_locale";

/** One year — a language choice is a preference, not a session. */
export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** Unknown or absent values resolve to the default, never throw. */
export function resolveLocale(value: unknown): Locale {
  return isLocale(value) ? value : DEFAULT_LOCALE;
}
