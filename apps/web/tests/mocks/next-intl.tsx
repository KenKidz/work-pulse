/**
 * Vitest stand-in for `next-intl` (aliased in `vitest.config.ts`).
 *
 * It resolves the REAL English catalog through `use-intl`'s core runtime, so
 * component tests keep proving the shipped copy (and ICU/interpolation)
 * without wrapping every render in a provider. Locale is always `en` in
 * tests; Vietnamese copy is covered by the catalog parity test.
 */

import { createFormatter, createTranslator } from "use-intl";
import type { ReactNode } from "react";

import messages from "@/messages/en";

export function NextIntlClientProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function IntlProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function useLocale(): "en" {
  return "en";
}

export function useMessages() {
  return messages;
}

export function useTranslations(namespace?: string) {
  return createTranslator({ locale: "en", messages, namespace: namespace as never });
}

export function useFormatter() {
  return createFormatter({ locale: "en", timeZone: "UTC" });
}

export function useNow(): Date {
  return new Date("2026-01-01T00:00:00Z");
}

export function useTimeZone(): string {
  return "UTC";
}
