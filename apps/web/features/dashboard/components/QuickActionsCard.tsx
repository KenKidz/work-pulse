"use client";

import { ChevronRight, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type { Role } from "@workpulse/api-client/index.schemas";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NAV_ITEMS } from "@/components/destinations";

/**
 * Finance lives in the payroll workspace; every other approver gets the
 * workforce surfaces first. The list is filtered by the same role gates as the
 * sidebar, so a link never appears for a role that cannot open it.
 */
const DEFAULT_QUICK_ACTIONS = [
  "/employees",
  "/attendance",
  "/reports",
  "/command-center",
  "/settings/users",
];

const QUICK_ACTIONS_BY_ROLE: Partial<Record<Role, string[]>> = {
  finance: ["/payroll", "/payroll/my-payslips", "/reports", "/attendance"],
};

export function QuickActionsCard({ role }: { role: Role }) {
  const t = useTranslations("dashboard.quickActions");
  const nav = useTranslations("nav");
  const hrefs = QUICK_ACTIONS_BY_ROLE[role] ?? DEFAULT_QUICK_ACTIONS;

  const items: Array<{ href: string; label: string; icon: LucideIcon }> = NAV_ITEMS.filter(
    (item) => hrefs.includes(item.href) && (item.roles === undefined || item.roles.includes(role)),
  )
    .slice(0, 4)
    .map((item) => ({
      href: item.href,
      label: nav(item.key),
      icon: item.icon,
    }));

  return (
    <Card data-testid="quick-actions" className="gap-0 rounded-md border-border py-0 shadow-none">
      <CardHeader className="px-5 pt-5 pb-3">
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="px-2 pb-2">
        <ul className="flex flex-col">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="flex min-h-11 items-center gap-3 rounded-sm px-3 py-2 text-sm font-medium outline-none hover:bg-muted/60 focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  <ChevronRight className="size-4 text-muted-foreground" aria-hidden="true" />
                </Link>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
