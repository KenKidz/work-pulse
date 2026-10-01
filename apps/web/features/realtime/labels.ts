/**
 * Human-facing labels for pinned realtime enums — wire values are never
 * rendered. Badge tones follow the semantic status tokens; the hint copy
 * repeats ADR 0009's overlapping-counts rule on the wall.
 */

import type { ComponentProps } from "react";

import type { Badge } from "@/components/ui/badge";

import type { PresenceRowStatus, RealtimeActivityEntryAction } from "./types";

type BadgeVariant = ComponentProps<typeof Badge>["variant"];

export const presenceStatusLabel: Record<PresenceRowStatus, string> = {
  present: "On site",
  remote: "Remote",
  checked_out: "Checked out",
  absent: "Absent",
  on_leave: "On leave",
};

export const presenceStatusVariant: Record<PresenceRowStatus, BadgeVariant> = {
  present: "success",
  remote: "muted",
  checked_out: "outline",
  absent: "destructive",
  on_leave: "secondary",
};

export const activityActionLabel: Record<RealtimeActivityEntryAction, string> = {
  checked_in: "checked in",
  checked_out: "checked out",
};
