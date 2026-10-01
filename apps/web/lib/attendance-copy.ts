/**
 * Pinned count-semantics copy shared by the attendance board and the realtime
 * command center (ADR 0009) — one source so the two surfaces can never
 * explain the same API behavior differently. Feature folders must not import
 * each other's internals, so shared copy lives here.
 */

/** Counts overlap, they are not a partition. */
export const countsOverlapHint =
  "Counts overlap: someone can be on site or remote and also be late. These numbers don’t add to a total.";

/** Rest-day over-count is accepted until individual rosters exist. */
export const absentCountHint =
  "May include people on a rest day; individual rosters aren’t tracked yet.";

/** The Leave context has not landed — the field stays in the contract. */
export const onLeaveCountHint = "Leave isn’t tracked yet, so this stays at 0.";
