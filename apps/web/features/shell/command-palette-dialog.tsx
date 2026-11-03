"use client";

/**
 * The palette dialog body, loaded on first open so `cmdk` stays out of the
 * dashboard's initial shell chunk. Destinations come from the same
 * `visibleNavGroups` predicate as the sidebar; the theme action closes the
 * palette after switching.
 */

import { Moon, Sun } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";

import { visibleNavGroups } from "@/components/destinations";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { useAuth } from "@/features/auth/components/AuthGate";

export function CommandPaletteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("nav");
  const common = useTranslations("common");
  const router = useRouter();
  const { user } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const groups = visibleNavGroups(user.role);
  const dark = resolvedTheme === "dark";

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("command.title")}
      description={t("command.description")}
    >
      <CommandInput placeholder={t("command.placeholder")} />
      <CommandList>
        <CommandEmpty>{t("command.empty")}</CommandEmpty>
        {groups.map((group) => (
          <CommandGroup key={group.key} heading={t(`groups.${group.key}`)}>
            {group.items.map((item) => (
              <CommandItem
                key={item.href}
                value={`${t(item.key)} ${item.href}`}
                onSelect={() => {
                  onOpenChange(false);
                  router.push(item.href);
                }}
              >
                <item.icon aria-hidden="true" />
                <span>{t(item.key)}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        ))}
        <CommandSeparator />
        <CommandGroup heading={t("command.actions")}>
          <CommandItem
            value={`${dark ? common("theme.switchToLight") : common("theme.switchToDark")} theme`}
            onSelect={() => {
              setTheme(dark ? "light" : "dark");
              onOpenChange(false);
            }}
          >
            {dark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            <span>{dark ? common("theme.switchToLight") : common("theme.switchToDark")}</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
