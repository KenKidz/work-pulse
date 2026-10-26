/**
 * Vitest stand-in for `next-intl/server` — only the functions a component
 * test could transitively import. Keeps the suite on the real English
 * catalog through `use-intl`'s core runtime.
 */

import { createFormatter, createTranslator } from "use-intl";

import messages from "@/messages/en";

export async function getTranslations(namespace?: string) {
  return createTranslator({ locale: "en", messages, namespace: namespace as never });
}

export async function getLocale(): Promise<"en"> {
  return "en";
}

export async function getMessages() {
  return messages;
}

export async function getFormatter() {
  return createFormatter({ locale: "en", timeZone: "UTC" });
}
