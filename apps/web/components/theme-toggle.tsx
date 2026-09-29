"use client";

/**
 * Light/dark switch for the app shell (next-themes). Renders a stable
 * placeholder until mounted so server and client markup agree.
 */

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";

const emptySubscribe = () => () => {};

/** `false` on the server, `true` after hydration — without a setState effect. */
function useMounted(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );
}

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useMounted();

  const dark = mounted && resolvedTheme === "dark";

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="size-10 rounded-sm"
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      {dark ? <Moon aria-hidden="true" /> : <Sun aria-hidden="true" />}
    </Button>
  );
}
