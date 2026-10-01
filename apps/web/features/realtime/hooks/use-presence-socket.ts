"use client";

/**
 * Hand-written WebSocket client for `WS /api/v1/realtime/board`. Frames are
 * not in OpenAPI — they mirror `contracts/realtime/board.v1.json`.
 *
 * Application frames patch the same Query-cache entry the REST snapshot uses
 * (`realtimeBoardQueryKey`), so server data never leaves the cache. A delta
 * whose `date` differs from the snapshot makes the client refetch instead of
 * patching (midnight rollover); no snapshot yet (REST still loading) resyncs
 * too. Delivery is at-most-once and every upsert is a full row, so redelivery
 * is harmless; a dropped socket reconnects with backoff and the server's
 * first frame after the handshake is always a fresh snapshot. The protocol
 * has no client→server frames in v1 (connection health is uvicorn's WS
 * ping/pong).
 */

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { apiBaseUrl } from "@workpulse/api-client/mutator/custom-fetch";
import { useEffect, useRef, useState } from "react";

import { invalidateRealtimeBoard, realtimeBoardQueryKey } from "../api";
import type {
  PresenceRow,
  RealtimeBoardSnapshot,
  RealtimeFrame,
  RealtimePresenceUpsertFrame,
  RealtimeSnapshotFrame,
} from "../types";

export type PresenceSocketStatus = "connecting" | "live" | "unavailable";

/** Injectable for tests; production builds the browser's WebSocket. */
export type SocketFactory = (url: string) => WebSocket;

const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 15_000;
const ACTIVITY_CAP = 20;

/** `http(s)://host` → `ws(s)://host/api/v1/realtime/board` (cookie handshake). */
export function realtimeBoardSocketUrl(): string {
  const url = new URL("/api/v1/realtime/board", apiBaseUrl());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/** The customFetch response shape the generated query caches. */
type BoardResponse = { data: RealtimeBoardSnapshot; status: 200; headers: Headers };

export function usePresenceSocket({
  enabled,
  socketFactory,
}: {
  enabled: boolean;
  socketFactory?: SocketFactory;
}) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<PresenceSocketStatus>("connecting");
  // Latest factory without making it an effect dependency: an inline callback
  // must not tear the socket down on every render.
  const factoryRef = useRef<SocketFactory | undefined>(socketFactory);
  useEffect(() => {
    factoryRef.current = socketFactory;
  }, [socketFactory]);

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function scheduleRetry() {
      if (disposed) return;
      attempt += 1;
      const delay = Math.min(RETRY_BASE_MS * 2 ** (attempt - 1), RETRY_MAX_MS);
      retryTimer = setTimeout(connect, delay);
    }

    function connect() {
      if (disposed) return;
      let next: WebSocket;
      try {
        const url = realtimeBoardSocketUrl();
        next = factoryRef.current ? factoryRef.current(url) : new WebSocket(url);
      } catch {
        // Constructing the socket itself failed — treat like a dropped
        // connection: keep the REST snapshot and retry with backoff.
        setStatus("unavailable");
        scheduleRetry();
        return;
      }
      socket = next;
      next.onopen = () => {
        attempt = 0;
        setStatus("live");
      };
      next.onmessage = (event: MessageEvent) => {
        if (typeof event.data !== "string") return;
        const frame = parseFrame(event.data);
        if (frame !== null) applyFrame(queryClient, frame);
      };
      next.onerror = () => {
        // Browsers follow an error with a close event; the close handler
        // owns the retry so a single failure cannot schedule twice.
        setStatus("unavailable");
      };
      next.onclose = () => {
        if (disposed) return;
        setStatus("unavailable");
        scheduleRetry();
      };
    }

    connect();

    return () => {
      disposed = true;
      if (retryTimer !== null) clearTimeout(retryTimer);
      if (socket !== null) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try {
          socket.close();
        } catch {
          // Already closed — nothing to release.
        }
      }
    };
  }, [enabled, queryClient]);

  return { status };
}

/**
 * Shape checks for every field the merge dereferences — unknown or malformed
 * frames are ignored, never applied, so a bad frame cannot throw inside the
 * message handler or corrupt the cache.
 */
function parseFrame(raw: string): RealtimeFrame | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    console.warn("Ignoring unparseable realtime frame");
    return null;
  }
  if (!isRecord(value)) {
    console.warn("Ignoring non-object realtime frame");
    return null;
  }

  if (value.type === "snapshot") {
    if (
      typeof value.date === "string" &&
      isRecord(value.counts) &&
      Array.isArray(value.presence) &&
      Array.isArray(value.activity)
    ) {
      return value as unknown as RealtimeSnapshotFrame;
    }
    console.warn("Ignoring malformed snapshot frame");
    return null;
  }

  if (value.type === "presence.upsert") {
    if (
      typeof value.date === "string" &&
      isRecord(value.counts) &&
      isRecord(value.row) &&
      typeof value.row.employee_id === "string" &&
      typeof value.occurred_at === "string" &&
      (value.reason === "checked_in" || value.reason === "checked_out")
    ) {
      return value as unknown as RealtimePresenceUpsertFrame;
    }
    console.warn("Ignoring malformed presence.upsert frame");
    return null;
  }

  console.warn("Ignoring unknown realtime frame type");
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Snapshot frames replace the cache entry; `presence.upsert` frames merge the
 * full row, take the server's recounted counts verbatim and prepend the
 * activity entry (deduplicated, capped) so redelivery is harmless.
 */
function applyFrame(queryClient: QueryClient, frame: RealtimeFrame): void {
  if (frame.type === "snapshot") {
    queryClient.setQueryData(realtimeBoardQueryKey, {
      data: snapshotBody(frame),
      status: 200,
      headers: new Headers(),
    } satisfies BoardResponse);
    return;
  }

  const current = queryClient.getQueryData<BoardResponse>(realtimeBoardQueryKey);
  if (current === undefined || current.data.date !== frame.date) {
    // No snapshot to patch against, or the workday rolled under us: resync.
    void invalidateRealtimeBoard(queryClient);
    return;
  }

  const entry = {
    occurred_at: frame.occurred_at,
    employee_id: frame.row.employee_id,
    action: frame.reason,
  };
  const activity = [
    entry,
    ...current.data.activity.filter(
      (item) =>
        !(
          item.employee_id === entry.employee_id &&
          item.action === entry.action &&
          item.occurred_at === entry.occurred_at
        ),
    ),
  ].slice(0, ACTIVITY_CAP);

  queryClient.setQueryData(realtimeBoardQueryKey, {
    ...current,
    data: {
      ...current.data,
      counts: frame.counts,
      presence: upsertRow(current.data.presence, frame.row),
      activity,
    },
  } satisfies BoardResponse);
}

function upsertRow(rows: PresenceRow[], row: PresenceRow): PresenceRow[] {
  const index = rows.findIndex((candidate) => candidate.employee_id === row.employee_id);
  if (index === -1) return [...rows, row];
  const next = [...rows];
  next[index] = row;
  return next;
}

/** Strip the frame discriminator — the REST body is the same payload. */
function snapshotBody(frame: RealtimeSnapshotFrame): RealtimeBoardSnapshot {
  return {
    date: frame.date,
    counts: frame.counts,
    presence: frame.presence,
    activity: frame.activity,
  };
}
