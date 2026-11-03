import { Activity } from "lucide-react";
import Link from "next/link";
import { cookies } from "next/headers";
import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { AuthGate } from "@/features/auth/components/AuthGate";
import { UserMenu } from "@/features/auth/components/UserMenu";
import { NotificationBell } from "@/features/notifications/components/NotificationBell";
import { AppSidebar } from "@/features/shell/AppSidebar";
import { CommandPalette } from "@/features/shell/CommandPalette";
import { SkipToMain } from "@/features/shell/SkipToMain";
import { LocaleSwitcher } from "@/components/locale-switcher";
import { ThemeToggle } from "@/components/theme-toggle";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  // The sidebar primitive persists its collapsed state in this cookie; reading
  // it here keeps server and client markup in agreement on first paint.
  const cookieStore = await cookies();
  const defaultOpen = cookieStore.get("sidebar_state")?.value !== "false";
  const common = await getTranslations("common");

  return (
    <AuthGate>
      <SkipToMain label={common("aria.skipToContent")} />
      <SidebarProvider defaultOpen={defaultOpen}>
        <AppSidebar />
        <SidebarInset>
          <header className="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-2 border-b border-border bg-background px-4 sm:gap-3 sm:px-6">
            <SidebarTrigger label={common("aria.toggleNavigation")} />
            <Separator orientation="vertical" className="h-6" />
            <Link
              href="/dashboard"
              className="flex shrink-0 items-center gap-2.5 rounded-sm font-semibold tracking-tight outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 md:hidden"
            >
              <Activity className="size-4.5 text-primary" aria-hidden="true" />
              <span>{common("appName")}</span>
            </Link>
            <div className="ml-auto flex items-center">
              <CommandPalette />
              <LocaleSwitcher />
              <NotificationBell />
              <ThemeToggle />
              <UserMenu />
            </div>
          </header>
          <main
            id="main"
            tabIndex={-1}
            className="mx-auto w-full max-w-7xl flex-1 px-4 py-7 outline-none sm:px-6 sm:py-9"
          >
            {children}
          </main>
        </SidebarInset>
      </SidebarProvider>
    </AuthGate>
  );
}
