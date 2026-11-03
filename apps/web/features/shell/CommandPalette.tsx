"use client";

/**
 * ⌘K command palette trigger. The trigger and shortcut stay in the shell
 * chunk; the dialog body (and `cmdk`) loads on first open. The shortcut is
 * ignored while a text control owns the keystroke and during IME composition.
 */

import { SearchIcon } from "lucide-react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useHotkey } from "@/hooks/use-hotkey";

const CommandPaletteDialog = dynamic(
  () => import("./command-palette-dialog").then((mod) => mod.CommandPaletteDialog),
  { ssr: false },
);

export function CommandPalette() {
  const common = useTranslations("common");
  const [open, setOpen] = useState(false);

  useHotkey(
    (event) => (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k",
    () => setOpen((current) => !current),
  );

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="hidden h-9 gap-2 px-3 md:inline-flex"
        onClick={() => setOpen(true)}
        aria-keyshortcuts="Meta+K Control+K"
      >
        <SearchIcon aria-hidden="true" />
        <span>{common("actions.search")}</span>
        <kbd
          aria-hidden="true"
          className="rounded-sm border border-border bg-muted px-1.5 font-mono text-xs text-muted-foreground"
        >
          ⌘K
        </kbd>
      </Button>
      {open ? <CommandPaletteDialog open={open} onOpenChange={setOpen} /> : null}
    </>
  );
}
