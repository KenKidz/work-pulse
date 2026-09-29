"use client";

/**
 * Primary navigation for the dashboard shell. Client leaf: the active item is
 * derived from the pathname; `aria-current="page"` marks it for assistive
 * technology too. Destinations come from the shared catalog.
 */

import { LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "cn";

import { DESTINATIONS } from "@/components/destinations";

const ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  ...DESTINATIONS.map(({ href, label, icon }) => ({ href, label, icon })),
];

export function MainNav({ className }: { className?: string }) {
  const pathname = usePathname();

  return (
    <nav aria-label="Main" className={cn("flex items-center gap-1", className)}>
      {ITEMS.map((item) => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
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
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
