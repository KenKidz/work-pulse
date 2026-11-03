"use client";

/**
 * One sidebar nav section. Client leaf: labels come from the `nav` namespace,
 * the active item is the longest-prefix match computed once by `AppSidebar`,
 * and a click closes the mobile sheet so the destination is visible. Expanded
 * rows keep a 44px touch target on mobile (`min-h-11`) and the dense 32px row
 * from `md` up; the collapsed rail falls back to the primitive's icon size and
 * exposes the label through a tooltip.
 */

import Link from "next/link";
import { useTranslations } from "next-intl";

import type { NavGroup as NavGroupData } from "@/components/destinations";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";

export function NavGroup({
  group,
  activeHref,
}: {
  group: NavGroupData;
  activeHref: string | null;
}) {
  const t = useTranslations("nav");
  const { setOpenMobile } = useSidebar();

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{t(`groups.${group.key}`)}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {group.items.map((item) => {
            const active = item.href === activeHref;
            return (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  asChild
                  isActive={active}
                  tooltip={t(item.key)}
                  className="min-h-11 md:min-h-8"
                >
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setOpenMobile(false)}
                  >
                    <item.icon aria-hidden="true" />
                    <span>{t(item.key)}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
