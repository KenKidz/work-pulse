/**
 * Presence status tones for the wall. Human-facing labels live in the
 * `realtime.status` / `realtime.activity` message namespaces; wire values
 * never reach the DOM (workpulse-ui §6).
 */

import type { ComponentProps } from "react";

import type { Badge } from "@/components/ui/badge";

import type { PresenceRowStatus } from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

export const presenceStatusVariant: Record<PresenceRowStatus, BadgeVariant> = {
  present: "success",
  remote: "muted",
  checked_out: "outline",
  absent: "destructive",
  on_leave: "secondary",
};
