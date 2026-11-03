"use client";

/**
 * The dashboard sidebar: brand, grouped navigation and the collapse rail.
 * The desktop rail collapses to icons (cookie-persisted by the primitive); on
 * mobile the primitive renders the same content in a focus-trapped sheet.
 * Destinations and their role gates come from `destinations.ts`; the active
 * route uses the shared longest-prefix rule so nested detail pages keep only
 * their own item marked.
 */

import { Activity } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";

import { activeNavHref, visibleNavGroups } from "@/components/destinations";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/features/auth/components/AuthGate";

import { NavGroup } from "./nav-group";

export function AppSidebar() {
  const t = useTranslations("nav");
  const common = useTranslations("common");
  const pathname = usePathname();
  const { user } = useAuth();
  const { setOpenMobile } = useSidebar();
  const groups = visibleNavGroups(user.role);
  const activeHref = activeNavHref(
    pathname,
    groups.flatMap((group) => group.items),
  );

  return (
    <Sidebar collapsible="icon" mobileLabel={t("menu")} mobileDescription={t("menuDescription")}>
      {/* Matches the 4rem app header so the brand cell's bottom border is the
          same continuous rule as the header border in both sidebar states
          (the collapsed rail's 32px button would otherwise leave a 48px cell). */}
      <SidebarHeader className="h-16 justify-center border-b border-border">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip={common("appName")}>
              <Link
                href="/dashboard"
                onClick={() => setOpenMobile(false)}
                className="group-data-[collapsible=icon]:justify-center"
              >
                <Activity className="text-primary" aria-hidden="true" />
                <span className="font-semibold tracking-tight group-data-[collapsible=icon]:hidden">
                  {common("appName")}
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <nav aria-label={common("aria.main")} className="flex flex-col gap-2">
          {groups.map((group) => (
            <NavGroup key={group.key} group={group} activeHref={activeHref} />
          ))}
        </nav>
      </SidebarContent>
      <SidebarRail label={common("aria.toggleNavigation")} />
    </Sidebar>
  );
}
