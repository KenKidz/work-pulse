/**
 * The only place a locale maps to its catalog. Static importers keep
 * Turbopack's module graph explicit (no dynamic `messages/${locale}.json`
 * specifier) and let TypeScript infer the message shape.
 */

import type { AbstractIntlMessages } from "next-intl";

import en from "@/messages/en";
import vi from "@/messages/vi";

import type { Locale } from "./config";

const CATALOGS = { en, vi } satisfies Record<Locale, AbstractIntlMessages>;

export function messagesFor(locale: Locale): AbstractIntlMessages {
  return CATALOGS[locale];
}
