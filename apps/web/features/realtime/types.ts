/**
 * Wire types for the realtime feature.
 *
 * The REST snapshot and the WS `snapshot` frame share one generated payload
 * (`RealtimeBoardSnapshot`). Frames are not part of OpenAPI — they are
 * validated against `contracts/realtime/board.v1.json` — so the frame union
 * is mirrored here by hand. Identifiers and state only: names are joined
 * client-side, never carried on the wire (`.ai/security.md`).
 */

import type {
  RealtimeActivityEntryAction,
  RealtimeBoardCounts,
  RealtimeBoardSnapshot,
  PresenceRow,
} from "@workpulse/api-client/index.schemas";

export type {
  Employee,
  EmployeeSummary,
  PresenceRow,
  PresenceRowStatus,
  RealtimeActivityEntry,
  RealtimeActivityEntryAction,
  RealtimeBoardCounts,
  RealtimeBoardSnapshot,
} from "@workpulse/api-client/index.schemas";

/** Server → client frame: the whole board, first after every handshake. */
export type RealtimeSnapshotFrame = RealtimeBoardSnapshot & { type: "snapshot" };

/** Presence reasons; activity is attendance-only (leave never enters the rail). */
export type RealtimePresenceReason = RealtimeActivityEntryAction | "leave_approved";

/** Server → client frame: one full-row idempotent upsert plus fresh counts. */
export type RealtimePresenceUpsertFrame = {
  type: "presence.upsert";
  date: string;
  counts: RealtimeBoardCounts;
  row: PresenceRow;
  occurred_at: string;
  reason: RealtimePresenceReason;
};

export type RealtimeFrame = RealtimeSnapshotFrame | RealtimePresenceUpsertFrame;
