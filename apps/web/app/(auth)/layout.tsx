import type { ReactNode } from "react";

import { LocaleSwitcher } from "@/components/locale-switcher";

/**
 * The `(auth)` route group: a quiet centered stage on the same paper surface
 * as the dashboard — the product's front door, not a marketing page. Each
 * page owns its card; this shell owns the column, the language switch and the
 * one-line product footer that anchors it. Server Component.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <div className="absolute top-4 right-4">
        <LocaleSwitcher />
      </div>
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
