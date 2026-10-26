"use client";

/**
 * Primary navigation for the dashboard shell. Client leaf: the active item is
 * derived from the pathname; `aria-current="page"` marks it for assistive
 * technology too. Destinations come from the shared catalog and their labels
 * from the `nav` message namespace.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "cn";

import { activeNavHref, NAV_ITEMS } from "@/components/destinations";
import { useAuth } from "@/features/auth/components/AuthGate";

export function MainNav({ className }: { className?: string }) {
  const t = useTranslations("nav");
  const common = useTranslations("common.aria");
  const pathname = usePathname();
  const { user } = useAuth();
  const items = NAV_ITEMS.filter(
    (item) => !item.roles || (user !== null && item.roles.includes(user.role)),
  );
  const activeHref = activeNavHref(pathname, items);

  return (
    <nav aria-label={common("main")} className={cn("flex min-w-0 flex-1 items-center gap-1 overflow-x-auto", className)}>
      {items.map((item) => {
        const active = item.href === activeHref;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "relative inline-flex h-full items-center gap-2 rounded-sm px-3 text-sm font-medium whitespace-nowrap transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
              active
                ? "text-foreground after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-primary after:content-['']"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <item.icon className="size-4" aria-hidden="true" />
            {t(item.key)}
          </Link>
        );
      })}
    </nav>
  );
}
