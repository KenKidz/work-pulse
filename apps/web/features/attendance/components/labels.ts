/**
 * Human-facing labels for pinned enums — wire values are never rendered.
 * Board copy explains the independent counts and the attendance data that is
 * not tracked yet.
 */

import type { BoardAction, WorkMode } from "../types";

export const workModeLabel: Record<WorkMode, string> = {
  onsite: "On site",
  remote: "Remote",
};

export const boardActionLabel: Record<BoardAction, string> = {
  checked_in: "checked in",
  checked_out: "checked out",
};

export const workModeHint =
  "Counts overlap: someone can be on site or remote and also be late. These numbers don’t add to a total.";

export const absentHint =
  "May include people on a rest day; individual rosters aren’t tracked yet.";

export const onLeaveHint = "Leave isn’t tracked yet, so this stays at 0.";
