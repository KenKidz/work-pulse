/**
 * CommandCenter feature tests (AC9–AC11) — the snapshot query is a real
 * `useQuery` over a controllable fixture, so socket patches are observable in
 * the UI; auth, the employee directory and routing are mocked. Covers render
 * (counts + joined names + rail), ordering, loading/empty/error/denied
 * states, the 60s refresh cadence, live frames and the degraded banner.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { User } from "@workpulse/api-client/index.schemas";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { useGetCurrentEmployee } from "@workpulse/api-client";

import { useAuth } from "@/features/auth/components/AuthGate";
import { useEmployees } from "@/features/employees/hooks/use-employees";
import { CommandCenter, DIRECTORY_PAGE_SIZE } from "@/features/realtime/components/CommandCenter";

import { employeesState, meState, socketHarness, wireQuery } from "./helpers";

const routerMocks = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => routerMocks }));
vi.mock("@/features/auth/components/AuthGate", () => ({ useAuth: vi.fn() }));
vi.mock("@/features/employees/hooks/use-employees", () => ({ useEmployees: vi.fn() }));

/**
 * The generated hook is mocked with a real `useQuery` over a controllable
 * fixture: `queryClient.setQueryData` from socket frames then flows back into
 * the component exactly as in production (a static mock would hide that).
 */
const boardMock = vi.hoisted(() => ({
  snapshot: null as unknown,
  pending: false,
  failure: null as unknown,
  queryFnCalls: 0,
  lastQueryOptions: null as Record<string, unknown> | null,
}));

vi.mock("@workpulse/api-client", async () => {
  const { useQuery } = await import("@tanstack/react-query");
  return {
    getGetRealtimeBoardQueryKey: () => ["/api/v1/realtime/board"],
    useGetRealtimeBoard: (options?: { query?: Record<string, unknown> }) => {
      boardMock.lastQueryOptions = options?.query ?? null;
      return useQuery({
        queryKey: ["/api/v1/realtime/board"],
        queryFn: async () => {
          boardMock.queryFnCalls += 1;
          if (boardMock.failure) throw boardMock.failure;
          if (boardMock.pending) return await new Promise<never>(() => {});
          return { data: boardMock.snapshot, status: 200, headers: new Headers() };
        },
        ...options?.query,
      });
    },
    useGetCurrentEmployee: vi.fn(),
  };
});

const TENANT = "01900000-0000-7000-8000-000000000001";
const MANAGER_EMPLOYEE_ID = "01900000-0000-7000-8000-00000000a001";
const WORKER_ID = "01900000-0000-7000-8000-00000000a002";
const BOB_ID = "01900000-0000-7000-8000-00000000a003";
const ABSENT_ID = "01900000-0000-7000-8000-00000000a004";

const MANAGER: User = {
  id: "01900000-0000-7000-8000-0000000000b1",
  tenant_id: TENANT,
  email: "manager@workpulse.test",
  role: "manager",
  employee_id: MANAGER_EMPLOYEE_ID,
  active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const WORKER: User = {
  ...MANAGER,
  id: "01900000-0000-7000-8000-0000000000b2",
  email: "worker@workpulse.test",
  role: "employee",
  employee_id: WORKER_ID,
};

function employeeSummary(id: string, code: string, first: string, last: string) {
  return {
    id,
    employee_code: code,
    first_name: first,
    last_name: last,
    email: `${code.toLowerCase()}@workpulse.test`,
    department_id: "01900000-0000-7000-8000-000000000d01",
    position_id: "01900000-0000-7000-8000-000000000201",
    location_id: "01900000-0000-7000-8000-000000000301",
    manager_id: MANAGER_EMPLOYEE_ID,
    employment_type: "full_time",
    status: "active",
    hired_at: "2026-01-15",
    terminated_at: null,
  };
}

const EMPLOYEES = [
  employeeSummary(WORKER_ID, "EMP-00002", "Wendy", "Worker"),
  employeeSummary(BOB_ID, "EMP-00003", "Bob", "Brown"),
];

/** The manager's own linked employee — not present in the reports-only list. */
const MANAGER_EMPLOYEE = employeeSummary(MANAGER_EMPLOYEE_ID, "EMP-00001", "Mary", "Manager");

const BOARD = {
  date: "2026-10-01",
  counts: { present: 1, remote: 1, late: 1, on_leave: 0, absent: 1, checked_out: 0 },
  presence: [
    {
      employee_id: WORKER_ID,
      status: "present",
      late: true,
      minutes_late: 12,
      since: "2026-10-01T08:12:00Z",
      attendance_id: "rec-1",
    },
    {
      employee_id: BOB_ID,
      status: "remote",
      late: false,
      minutes_late: null,
      since: "2026-10-01T08:00:00Z",
      attendance_id: "rec-2",
    },
    {
      employee_id: ABSENT_ID,
      status: "absent",
      late: false,
      minutes_late: null,
      since: null,
      attendance_id: null,
    },
  ],
  activity: [{ occurred_at: "2026-10-01T08:12:00Z", employee_id: WORKER_ID, action: "checked_in" }],
};

const UPSERT = {
  type: "presence.upsert",
  date: BOARD.date,
  counts: { present: 2, remote: 0, late: 1, on_leave: 0, absent: 1, checked_out: 0 },
  row: {
    employee_id: BOB_ID,
    status: "present",
    late: false,
    minutes_late: null,
    since: "2026-10-01T09:00:00Z",
    attendance_id: "rec-2",
  },
  occurred_at: "2026-10-01T09:00:00Z",
  reason: "checked_in",
};

const mockUseAuth = vi.mocked(useAuth);

function renderCommandCenter() {
  const { sockets, urls, factory } = socketHarness();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <CommandCenter socketFactory={factory} />
    </QueryClientProvider>,
  );
  return { ...view, sockets, urls, queryClient };
}

/** The `presence-*` row containing a display name. */
function rowFor(name: string): HTMLElement {
  const table = screen.getByTestId("presence-table");
  const row = within(table)
    .getAllByRole("row")
    .find((candidate) => within(candidate).queryByText(name) !== null);
  if (!row) throw new Error(`No presence row for ${name}`);
  return row;
}

beforeEach(() => {
  vi.clearAllMocks();
  boardMock.snapshot = BOARD;
  boardMock.pending = false;
  boardMock.failure = null;
  boardMock.queryFnCalls = 0;
  boardMock.lastQueryOptions = null;
  mockUseAuth.mockReturnValue({ user: MANAGER });
  wireQuery(useEmployees, employeesState(EMPLOYEES));
  wireQuery(useGetCurrentEmployee, meState(MANAGER_EMPLOYEE));
});

describe("CommandCenter", () => {
  it("renders counts, joined names and the activity rail from the snapshot", async () => {
    renderCommandCenter();

    await screen.findByTestId("presence-table");
    expect(screen.getByTestId("count-present")).toHaveTextContent("1");
    expect(screen.getByTestId("count-remote")).toHaveTextContent("1");
    expect(screen.getByTestId("count-late")).toHaveTextContent("1");
    expect(screen.getByTestId("count-absent")).toHaveTextContent("1");
    expect(screen.getByTestId("count-checked-out")).toHaveTextContent("0");
    expect(screen.getByTestId("count-on-leave")).toHaveTextContent("0");

    const table = screen.getByTestId("presence-table");
    expect(within(table).getByText("Wendy Worker")).toBeInTheDocument();
    expect(within(table).getByText("EMP-00002")).toBeInTheDocument();
    expect(within(table).getByText("Late · 12m")).toBeInTheDocument();
    // Unknown ids fall back to a stable short code — frames carry no names.
    expect(within(table).getByText("Employee …a004")).toBeInTheDocument();

    const rail = screen.getByTestId("live-activity");
    expect(within(rail).getByText("Wendy Worker")).toBeInTheDocument();
    expect(within(rail).getByText("checked in")).toBeInTheDocument();
  });

  it("orders working rows before absent rows", async () => {
    renderCommandCenter();

    await screen.findByTestId("presence-table");
    const table = screen.getByTestId("presence-table");
    const rows = within(table).getAllByRole("row").slice(1);

    expect(within(rows[0]!).getByText("Wendy Worker")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("On site")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Bob Brown")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Employee …a004")).toBeInTheDocument();
  });

  it("joins the manager's own row from the current-employee endpoint", async () => {
    boardMock.snapshot = {
      ...BOARD,
      presence: [
        ...BOARD.presence,
        {
          employee_id: MANAGER_EMPLOYEE_ID,
          status: "remote",
          late: false,
          minutes_late: null,
          since: "2026-10-01T08:30:00Z",
          attendance_id: "rec-me",
        },
      ],
      activity: [
        {
          occurred_at: "2026-10-01T08:30:00Z",
          employee_id: MANAGER_EMPLOYEE_ID,
          action: "checked_in",
        },
        ...BOARD.activity,
      ],
    };

    renderCommandCenter();

    await screen.findByTestId("presence-table");
    // GET /employees is reports-only for managers; the /me merge names self.
    expect(
      within(screen.getByTestId("presence-table")).getByText("Mary Manager"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("live-activity")).getByText("Mary Manager"),
    ).toBeInTheDocument();
    expect(vi.mocked(useGetCurrentEmployee)).toHaveBeenCalledWith({
      query: { enabled: true },
    });
  });

  it("shows the live banner once the socket opens", async () => {
    const { sockets } = renderCommandCenter();

    expect(screen.getByText("Connecting to live updates…")).toBeInTheDocument();

    act(() => sockets[0]!.emitOpen());

    expect(await screen.findByText("Live")).toBeInTheDocument();
    expect(screen.getByText(/appear here within seconds/i)).toBeInTheDocument();
  });

  it("patches counts, rows and the rail from a presence.upsert frame", async () => {
    const { sockets } = renderCommandCenter();
    await screen.findByTestId("presence-table");

    act(() => sockets[0]!.emitFrame(UPSERT));

    await waitFor(() => expect(screen.getByTestId("count-present")).toHaveTextContent("2"));
    expect(screen.getByTestId("count-remote")).toHaveTextContent("0");
    expect(within(rowFor("Bob Brown")).getByText("On site")).toBeInTheDocument();

    const items = within(screen.getByTestId("live-activity")).getAllByRole("listitem");
    expect(within(items[0]!).getByText("Bob Brown")).toBeInTheDocument();
    expect(within(items[1]!).getByText("Wendy Worker")).toBeInTheDocument();
  });

  it("refetches instead of patching when the frame's workday rolled", async () => {
    const { sockets } = renderCommandCenter();
    await screen.findByTestId("presence-table");
    const before = boardMock.queryFnCalls;

    act(() => sockets[0]!.emitFrame({ ...UPSERT, date: "2026-10-02" }));

    await waitFor(() => expect(boardMock.queryFnCalls).toBeGreaterThan(before));
    // The fixture still describes the current workday.
    expect(screen.getByTestId("count-present")).toHaveTextContent("1");
  });

  it("shows the degraded banner on a dropped socket and keeps the snapshot", async () => {
    const { sockets } = renderCommandCenter();
    await screen.findByTestId("presence-table");

    act(() => sockets[0]!.emitClose());

    expect(await screen.findByText("Live updates unavailable")).toBeInTheDocument();
    expect(screen.getByText(/refreshes about once a minute/i)).toBeInTheDocument();
    expect(screen.getByTestId("presence-table")).toBeInTheDocument();
  });

  it("shows the loading state while the snapshot resolves", () => {
    boardMock.pending = true;
    renderCommandCenter();

    expect(screen.getByText("Loading the live board…")).toBeInTheDocument();
  });

  it("renders the empty state for a quiet tenant", async () => {
    boardMock.snapshot = {
      date: BOARD.date,
      counts: { present: 0, remote: 0, late: 0, on_leave: 0, absent: 0, checked_out: 0 },
      presence: [],
      activity: [],
    };
    renderCommandCenter();

    expect(await screen.findByText(/no one is on the board yet today/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing yet today/i)).toBeInTheDocument();
  });

  it("redirects a non-allowed role without fetching the board", async () => {
    mockUseAuth.mockReturnValue({ user: WORKER });
    renderCommandCenter();

    expect(screen.getByText("Checking access…")).toBeInTheDocument();
    await waitFor(() => expect(routerMocks.replace).toHaveBeenCalledWith("/dashboard"));
    expect(boardMock.queryFnCalls).toBe(0);
    expect(screen.queryByTestId("presence-table")).not.toBeInTheDocument();
  });

  it("renders the denied alert on a 403 without a retry", async () => {
    boardMock.failure = new ApiError(403, "forbidden", "Insufficient role");

    renderCommandCenter();

    expect(await screen.findByText("Command center unavailable")).toBeInTheDocument();
    expect(screen.getByText(/managers, hr and admins can watch/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("renders the error state with a working retry", async () => {
    boardMock.failure = new ApiError(500, "internal_error", "Boom");
    renderCommandCenter();

    expect(await screen.findByText("Could not load the live board")).toBeInTheDocument();

    boardMock.failure = null;
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByTestId("presence-table")).toBeInTheDocument();
  });

  it("refreshes the snapshot on the 60s cadence and requests one directory page", async () => {
    renderCommandCenter();
    await screen.findByTestId("presence-table");

    expect(boardMock.lastQueryOptions).toMatchObject({
      refetchInterval: 60_000,
      enabled: true,
    });
    expect(useEmployees).toHaveBeenCalledWith({ page_size: DIRECTORY_PAGE_SIZE });
  });
});
