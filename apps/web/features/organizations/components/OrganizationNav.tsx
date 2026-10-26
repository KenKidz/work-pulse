"use client";

/**
 * Tab navigation for the organization settings section. Client leaf — the
 * surrounding layout stays a Server Component.
 */

import { cn } from "cn";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

const TABS = [
  { href: "/settings/organization/departments", key: "departments" },
  { href: "/settings/organization/positions", key: "positions" },
  { href: "/settings/organization/locations", key: "locations" },
] as const;

export function OrganizationNav() {
  const t = useTranslations("organizations.settings");
  const pathname = usePathname();

  return (
    <nav aria-label={t("navLabel")} className="flex gap-4 border-b">
      {TABS.map((tab) => {
        const active = pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "border-b-2 px-1 pb-2 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
              active
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
            )}
          >
            {t(tab.key)}
          </Link>
        );
      })}
    </nav>
  );
}
