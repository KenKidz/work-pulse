/**
 * Human-facing labels for pinned enums — wire values are never rendered.
 * Board copy explains the independent counts and the attendance data that is
 * not tracked yet; the count-semantics strings are shared with the realtime
 * command center through `lib/attendance-copy.ts` (ADR 0009).
 */

import { absentCountHint, countsOverlapHint, onLeaveCountHint } from "@/lib/attendance-copy";

import type { BoardAction, WorkMode } from "../types";

export const workModeLabel: Record<WorkMode, string> = {
  onsite: "On site",
  remote: "Remote",
};

export const boardActionLabel: Record<BoardAction, string> = {
  checked_in: "checked in",
  checked_out: "checked out",
};

export const workModeHint = countsOverlapHint;

export const absentHint = absentCountHint;

export const onLeaveHint = onLeaveCountHint;
