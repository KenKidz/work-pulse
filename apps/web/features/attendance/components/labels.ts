/**
 * Human-facing labels for pinned enums — wire values are never rendered.
 * Board copy states the two pinned limitations the spec requires:
 * counts overlap (`late` is orthogonal to presence) and `absent` over-counts
 * on rest days until rosters/Leave land (Decision #7).
 */

import type { BoardAction, WorkMode } from "../types";

export const workModeLabel: Record<WorkMode, string> = {
  onsite: "Onsite",
  remote: "Remote",
};

export const boardActionLabel: Record<BoardAction, string> = {
  checked_in: "checked in",
  checked_out: "checked out",
};

export const workModeHint =
  "Late employees also appear in Present/Remote — the counts overlap by design.";

export const absentHint =
  "Absent counts anyone whose expected shift has started with no check-in yet; until leave and rosters exist (Phase 3) it over-counts on rest days.";

export const onLeaveHint = "Always 0 until the Leave feature lands (Phase 3).";
