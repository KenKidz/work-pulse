import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { LOCALE_COOKIE, resolveLocale } from "./config";
import { messagesFor } from "./messages";

/**
 * Request-scoped next-intl config. The locale comes from the `wp_locale`
 * cookie; an unknown/absent value falls back to English. Returning `locale`
 * explicitly is required for the no-routing (cookie) setup.
 */
export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const locale = resolveLocale(cookieStore.get(LOCALE_COOKIE)?.value);

  return {
    locale,
    messages: messagesFor(locale),
  };
});
