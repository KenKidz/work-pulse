/**
 * `usePresenceSocket` tests — the hook runs against a real QueryClient and a
 * scripted WebSocket double (no UI mocks), covering the connection lifecycle,
 * snapshot replacement, upsert merging (row/counts/activity, dedupe, cap),
 * the date-rollover refetch, malformed-frame tolerance and backoff reconnect.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiBaseUrl } from "@workpulse/api-client/mutator/custom-fetch";

import { realtimeBoardQueryKey } from "@/features/realtime/api";
import {
  usePresenceSocket,
  type SocketFactory,
} from "@/features/realtime/hooks/use-presence-socket";
import type { PresenceRow, RealtimeBoardSnapshot } from "@/features/realtime/types";

import { socketHarness } from "./helpers";

vi.mock("@workpulse/api-client", () => ({
  getGetRealtimeBoardQueryKey: () => ["/api/v1/realtime/board"],
}));

const WORKER_ID = "01900000-0000-7000-8000-00000000a002";

function row(employeeId: string, status: PresenceRow["status"]): PresenceRow {
  return {
    employee_id: employeeId,
    status,
    late: false,
    minutes_late: null,
    since: "2026-10-01T08:00:00Z",
    attendance_id: "rec-1",
  };
}

function board(overrides: Partial<RealtimeBoardSnapshot> = {}): RealtimeBoardSnapshot {
  return {
    date: "2026-10-01",
    counts: { present: 1, remote: 0, late: 0, on_leave: 0, absent: 0, checked_out: 0 },
    presence: [row(WORKER_ID, "present")],
    activity: [],
    ...overrides,
  };
}

function Harness({ factory, enabled = true }: { factory: SocketFactory; enabled?: boolean }) {
  const { status } = usePresenceSocket({ enabled, socketFactory: factory });
  return <span data-testid="socket-status">{status}</span>;
}

function setup(options: { data?: RealtimeBoardSnapshot; enabled?: boolean } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (options.data !== undefined) {
    queryClient.setQueryData(realtimeBoardQueryKey, {
      data: options.data,
      status: 200,
      headers: new Headers(),
    });
  }
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  const { sockets, urls, factory } = socketHarness();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <Harness factory={factory} enabled={options.enabled ?? true} />
    </QueryClientProvider>,
  );
  return { queryClient, invalidateSpy, sockets, urls, ...view };
}

function cached(queryClient: QueryClient): { data: RealtimeBoardSnapshot } | undefined {
  return queryClient.getQueryData<{ data: RealtimeBoardSnapshot }>(realtimeBoardQueryKey);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("usePresenceSocket", () => {
  it("connects to the ws board URL derived from the API base URL", () => {
    const { urls } = setup();

    const expected = `${apiBaseUrl().replace(/^http/, "ws")}/api/v1/realtime/board`;
    expect(urls).toEqual([expected]);
  });

  it("reports connecting → live → unavailable across the socket lifecycle", () => {
    const { sockets } = setup();
    expect(screen.getByTestId("socket-status")).toHaveTextContent("connecting");

    act(() => sockets[0]!.emitOpen());
    expect(screen.getByTestId("socket-status")).toHaveTextContent("live");

    act(() => sockets[0]!.emitClose());
    expect(screen.getByTestId("socket-status")).toHaveTextContent("unavailable");
  });

  it("replaces the cache from the snapshot frame, stripping the discriminator", () => {
    const { sockets, queryClient } = setup();
    const snapshot = board({ date: "2026-10-02" });

    act(() => sockets[0]!.emitFrame({ type: "snapshot", ...snapshot }));

    const data = cached(queryClient)!.data;
    expect(data).toEqual(snapshot);
    expect(data).not.toHaveProperty("type");
  });

  it("applies an upsert: full row replace, server counts, activity prepend", () => {
    const { sockets, queryClient } = setup({
      data: board({
        presence: [row(WORKER_ID, "remote")],
        activity: [
          { occurred_at: "2026-10-01T07:00:00Z", employee_id: WORKER_ID, action: "checked_in" },
        ],
      }),
    });

    act(() =>
      sockets[0]!.emitFrame({
        type: "presence.upsert",
        date: "2026-10-01",
        counts: { present: 1, remote: 0, late: 1, on_leave: 0, absent: 0, checked_out: 0 },
        row: { ...row(WORKER_ID, "present"), late: true, minutes_late: 5 },
        occurred_at: "2026-10-01T09:30:00Z",
        reason: "checked_in",
      }),
    );

    const data = cached(queryClient)!.data;
    expect(data.counts).toEqual({
      present: 1,
      remote: 0,
      late: 1,
      on_leave: 0,
      absent: 0,
      checked_out: 0,
    });
    expect(data.presence).toHaveLength(1);
    expect(data.presence[0]).toMatchObject({ status: "present", late: true, minutes_late: 5 });
    expect(data.activity).toHaveLength(2);
    expect(data.activity[0]).toEqual({
      occurred_at: "2026-10-01T09:30:00Z",
      employee_id: WORKER_ID,
      action: "checked_in",
    });
  });

  it("deduplicates redelivered upserts and caps the rail at 20", () => {
    const activity = Array.from({ length: 20 }, (_, index) => ({
      occurred_at: `2026-10-01T07:${String(index).padStart(2, "0")}:00Z`,
      employee_id: WORKER_ID,
      action: index % 2 === 0 ? ("checked_in" as const) : ("checked_out" as const),
    }));
    const { sockets, queryClient } = setup({ data: board({ activity }) });
    const frame = {
      type: "presence.upsert",
      date: "2026-10-01",
      counts: { present: 1, remote: 0, late: false, on_leave: 0, absent: 0, checked_out: 0 },
      row: row(WORKER_ID, "present"),
      occurred_at: "2026-10-01T09:30:00Z",
      reason: "checked_in",
    };

    act(() => sockets[0]!.emitFrame(frame));
    act(() => sockets[0]!.emitFrame(frame));

    const data = cached(queryClient)!.data;
    expect(data.activity).toHaveLength(20);
    expect(data.activity[0]?.occurred_at).toBe("2026-10-01T09:30:00Z");
    expect(
      data.activity.filter((entry) => entry.occurred_at === "2026-10-01T09:30:00Z"),
    ).toHaveLength(1);
  });

  it("refetches instead of patching when the frame date differs", () => {
    const initial = board();
    const { sockets, queryClient, invalidateSpy } = setup({ data: initial });

    act(() =>
      sockets[0]!.emitFrame({
        type: "presence.upsert",
        date: "2026-10-02",
        counts: { present: 0, remote: 0, late: 0, on_leave: 0, absent: 0, checked_out: 1 },
        row: row(WORKER_ID, "checked_out"),
        occurred_at: "2026-10-01T23:58:00Z",
        reason: "checked_out",
      }),
    );

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: realtimeBoardQueryKey });
    expect(cached(queryClient)!.data).toEqual(initial);
  });

  it("requests a resync when an upsert arrives before any snapshot", () => {
    const { sockets, invalidateSpy } = setup();

    act(() =>
      sockets[0]!.emitFrame({
        type: "presence.upsert",
        date: "2026-10-01",
        counts: { present: 1, remote: 0, late: 0, on_leave: 0, absent: 0, checked_out: 0 },
        row: row(WORKER_ID, "present"),
        occurred_at: "2026-10-01T09:30:00Z",
        reason: "checked_in",
      }),
    );

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: realtimeBoardQueryKey });
  });

  it("ignores malformed and unknown frames", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { sockets, queryClient } = setup();

    act(() => sockets[0]!.emitRaw("not json"));
    act(() => sockets[0]!.emitFrame({ type: "something.else", date: "2026-10-01" }));
    // Structurally incomplete upsert (no row) with a matching date — the old
    // shape check would have thrown on `frame.row.employee_id`.
    act(() => sockets[0]!.emitFrame({ type: "presence.upsert", date: "2026-10-01" }));

    expect(cached(queryClient)).toBeUndefined();
    expect(warn).toHaveBeenCalledTimes(3);
  });

  it("reconnects with backoff and resyncs from the next snapshot", () => {
    vi.useFakeTimers();
    try {
      const { sockets, queryClient } = setup();

      act(() => sockets[0]!.emitClose());
      expect(screen.getByTestId("socket-status")).toHaveTextContent("unavailable");

      act(() => {
        vi.advanceTimersByTime(1_000);
      });
      expect(sockets).toHaveLength(2);

      act(() => sockets[1]!.emitOpen());
      act(() => sockets[1]!.emitFrame({ type: "snapshot", ...board({ date: "2026-10-02" }) }));

      expect(screen.getByTestId("socket-status")).toHaveTextContent("live");
      expect(cached(queryClient)!.data.date).toBe("2026-10-02");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not connect while disabled", () => {
    const { sockets, urls } = setup({ enabled: false });

    expect(urls).toHaveLength(0);
    expect(sockets).toHaveLength(0);
    expect(screen.getByTestId("socket-status")).toHaveTextContent("connecting");
  });

  it("closes the socket on unmount", () => {
    const { sockets, unmount } = setup();

    unmount();

    expect(sockets[0]!.closed).toBe(true);
  });
});
