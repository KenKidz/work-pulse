/**
 * Shared test plumbing for the realtime suites — generated client hooks, the
 * auth context, the employee directory and routing are mocked in each suite;
 * hooks, frame application and components are the real thing (pattern from
 * the attendance suites).
 */

import { vi } from "vitest";

/** Wire a mocked generated query hook to a state object. */
export function wireQuery(hook: unknown, state: unknown): void {
  (hook as ReturnType<typeof vi.fn>).mockReturnValue(state);
}

/** Employees list state — the client-side directory join source. */
export function employeesState(items: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    data: {
      data: {
        items,
        page: { page: 1, page_size: 100, total_items: items.length, total_pages: 1 },
      },
      status: 200,
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

/** `/employees/me` state — the session user's linked employee (name join). */
export function meState(employee: unknown, overrides: Record<string, unknown> = {}) {
  return {
    data: { data: employee, status: 200 },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

/** A scripted WebSocket double: tests drive open/frame/close by hand. */
export class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {}

  close(): void {
    this.closed = true;
  }

  emitOpen(): void {
    this.onopen?.();
  }

  emitFrame(frame: unknown): void {
    this.emitRaw(JSON.stringify(frame));
  }

  emitRaw(data: string): void {
    this.onmessage?.({ data });
  }

  emitClose(): void {
    this.onclose?.();
  }
}

/** A factory handing out FakeSocket instances one connect at a time. */
export function socketHarness() {
  const sockets: FakeSocket[] = [];
  const urls: string[] = [];
  const factory = (url: string): WebSocket => {
    urls.push(url);
    const socket = new FakeSocket(url);
    sockets.push(socket);
    return socket as unknown as WebSocket;
  };
  return { sockets, urls, factory };
}
