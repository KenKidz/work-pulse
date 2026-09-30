import { Activity } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Authenticated-area counterpart: the `(auth)` route group. A quiet centered
 * column on the same paper surface as the dashboard — the product's front
 * door, not a marketing page. Server Component.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2.5">
          <span
            className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground"
            aria-hidden="true"
          >
            <Activity className="size-4" />
          </span>
          <span className="font-semibold tracking-tight">WorkPulse</span>
        </div>
        {children}
      </div>
    </main>
  );
}
