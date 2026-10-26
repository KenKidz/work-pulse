import type { Locale } from "./config";

import messages from "@/messages/en";

declare module "next-intl" {
  interface AppConfig {
    Locale: Locale;
    Messages: typeof messages;
  }
}
