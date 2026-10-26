"use client";

/**
 * Mobile navigation drawer for the dashboard shell. Client leaf: the open
 * state is local, the active item derives from the pathname, and the
 * destinations come from the shared catalog so they cannot drift from the
 * desktop nav.
 */

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { cn } from "cn";

import { activeNavHref, NAV_ITEMS } from "@/components/destinations";
import { useAuth } from "@/features/auth/components/AuthGate";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

export function MobileNav() {
  const t = useTranslations("nav");
  const common = useTranslations("common.aria");
  const pathname = usePathname();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const items = NAV_ITEMS.filter(
    (item) => !item.roles || (user !== null && item.roles.includes(user.role)),
  );
  const activeHref = activeNavHref(pathname, items);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-11 md:hidden"
          aria-label={common("openNavigation")}
          aria-expanded={open}
        >
          <Menu aria-hidden="true" />
        </Button>
      </SheetTrigger>
      <SheetContent side="right" showCloseButton={false}>
        <SheetHeader className="h-16 py-0">
          <div className="flex flex-col gap-1">
            <SheetTitle>{t("menu")}</SheetTitle>
            <SheetDescription className="sr-only">{t("menuDescription")}</SheetDescription>
          </div>
          <SheetClose asChild>
            <Button variant="ghost" size="icon" className="size-11" aria-label={common("close")}>
              <X aria-hidden="true" />
            </Button>
          </SheetClose>
        </SheetHeader>
        <nav aria-label={common("main")} className="flex flex-col gap-1 p-3">
          {items.map((item) => {
            const active = item.href === activeHref;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex min-h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <item.icon className="size-4 shrink-0" aria-hidden="true" />
                {t(item.key)}
              </Link>
            );
          })}
        </nav>
      </SheetContent>
    </Sheet>
  );
}
