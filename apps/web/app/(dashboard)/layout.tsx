import { Activity } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { AuthGate } from "@/features/auth/components/AuthGate";
import { UserMenu } from "@/features/auth/components/UserMenu";
import { MobileNav } from "@/components/mobile-nav";
import { MainNav } from "@/components/main-nav";
import { ThemeToggle } from "@/components/theme-toggle";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <div className="flex min-h-dvh flex-col">
        <header className="sticky top-0 z-40 border-b border-border bg-background">
          <div className="mx-auto flex h-16 w-full max-w-7xl items-center gap-6 px-4 sm:px-6">
            <Link
              href="/dashboard"
              className="flex shrink-0 items-center gap-2.5 rounded-sm font-semibold tracking-tight outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <Activity className="size-4.5 text-primary" aria-hidden="true" />
              WorkPulse
            </Link>
            <MainNav className="hidden h-16 md:flex" />
            <div className="ml-auto flex items-center">
              <ThemeToggle />
              <UserMenu />
              <MobileNav />
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-7 sm:px-6 sm:py-9">
          {children}
        </main>
      </div>
    </AuthGate>
  );
}
