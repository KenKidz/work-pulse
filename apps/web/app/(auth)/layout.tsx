import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";

import { LocaleSwitcher } from "@/components/locale-switcher";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * The `(auth)` route group: a quiet centered stage on the same paper surface
 * as the dashboard — the product's front door, not a marketing page. Each
 * page owns its card; this shell owns the column, the grouped utility chrome
 * and the one-line product footer that anchors it. Server Component.
 */
export default async function AuthLayout({ children }: { children: ReactNode }) {
  const t = await getTranslations("auth");

  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <div className="absolute top-4 right-4 flex items-center gap-0.5 rounded-md border border-border bg-card p-0.5">
        <LocaleSwitcher />
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm">{children}</div>
      <p className="mt-6 text-center text-xs text-muted-foreground">{t("footer")}</p>
    </main>
  );
}
