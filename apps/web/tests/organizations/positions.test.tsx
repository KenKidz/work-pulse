/**
 * Positions feature tests — list + create happy path, and the pinned
 * `position_title_taken` 409 rendered as a visible field error.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  useArchivePosition,
  useCreatePosition,
  useListPositions,
  useRestorePosition,
  useUpdatePosition,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { PositionsTable } from "@/features/organizations/components/PositionsTable";
import type { Position } from "@/features/organizations/types";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getListDepartmentsQueryKey: () => ["/api/v1/departments"],
  getListPositionsQueryKey: () => ["/api/v1/positions"],
  getListLocationsQueryKey: () => ["/api/v1/locations"],
  useListPositions: vi.fn(),
  useCreatePosition: vi.fn(),
  useUpdatePosition: vi.fn(),
  useArchivePosition: vi.fn(),
  useRestorePosition: vi.fn(),
}));

const SENIOR_NURSE: Position = {
  id: "00000000-0000-4000-8000-0000000e0001",
  tenant_id: "00000000-0000-4000-8000-000000000001",
  title: "Senior Nurse",
  description: "Ward lead",
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function listState(items: Position[], overrides: Record<string, unknown> = {}) {
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

type MutateFn = Mock<(variables: unknown) => Promise<unknown>>;

const mutateFns = {
  create: vi.fn<(variables: unknown) => Promise<unknown>>(),
  update: vi.fn<(variables: unknown) => Promise<unknown>>(),
  archive: vi.fn<(variables: unknown) => Promise<unknown>>(),
  restore: vi.fn<(variables: unknown) => Promise<unknown>>(),
};

type MutationOptionsLike = {
  mutation?: {
    onSuccess?: (data: unknown, variables: unknown, context: unknown) => unknown;
    onError?: (error: unknown, variables: unknown, context: unknown) => unknown;
  };
};

function wireMutation(hook: unknown, mutateAsync: MutateFn) {
  const impl = (options?: MutationOptionsLike) => {
    async function run(variables: unknown, rethrow: boolean) {
      try {
        const result = await mutateAsync(variables);
        await options?.mutation?.onSuccess?.(result, variables, undefined);
        return result;
      } catch (error) {
        await options?.mutation?.onError?.(error, variables, undefined);
        if (rethrow) throw error;
        return undefined;
      }
    }
    return {
      mutate: (variables: unknown) => {
        void run(variables, false);
      },
      mutateAsync: (variables: unknown) => run(variables, true),
      isPending: false,
    };
  };
  (hook as ReturnType<typeof vi.fn>).mockImplementation(impl as never);
}

function renderTable() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <PositionsTable />
    </QueryClientProvider>,
  );
  return { queryClient, invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useCreatePosition, mutateFns.create);
  wireMutation(useUpdatePosition, mutateFns.update);
  wireMutation(useArchivePosition, mutateFns.archive);
  wireMutation(useRestorePosition, mutateFns.restore);
  vi.mocked(useListPositions).mockReturnValue(listState([]) as never);
});

describe("PositionsTable", () => {
  it("renders fetched positions", async () => {
    vi.mocked(useListPositions).mockReturnValue(listState([SENIOR_NURSE]) as never);

    renderTable();

    expect(await screen.findByText("Senior Nurse")).toBeInTheDocument();
    expect(screen.getByText("Ward lead")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("creates a position, invalidates the list and toasts", async () => {
    const user = userEvent.setup();
    mutateFns.create.mockResolvedValue({
      data: { data: { ...SENIOR_NURSE, title: "Junior Nurse" }, status: 201 },
    });
    const { invalidateSpy } = renderTable();

    await user.click(await screen.findByRole("button", { name: /new position/i }));
    await user.type(screen.getByLabelText("Title"), "Junior Nurse");
    await user.type(screen.getByLabelText("Description"), "Assists the ward lead");
    await user.click(screen.getByRole("button", { name: /create position/i }));

    await waitFor(() =>
      expect(mutateFns.create).toHaveBeenCalledWith({
        data: { title: "Junior Nurse", description: "Assists the ward lead" },
      }),
    );
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["/api/v1/positions"] }),
    );
    expect(toast.success).toHaveBeenCalledWith("Position created");
  });

  it("renders a 409 title-taken failure as a visible field error", async () => {
    const user = userEvent.setup();
    mutateFns.create.mockRejectedValue(
      new ApiError(409, "position_title_taken", "Position title already taken in this tenant"),
    );
    renderTable();

    await user.click(await screen.findByRole("button", { name: /new position/i }));
    await user.type(screen.getByLabelText("Title"), "Senior Nurse");
    await user.click(screen.getByRole("button", { name: /create position/i }));

    expect(
      await screen.findByText("This title is already used by another position."),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("Position title already taken in this tenant");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("archives a position and invalidates the list", async () => {
    const user = userEvent.setup();
    vi.mocked(useListPositions).mockReturnValue(listState([SENIOR_NURSE]) as never);
    mutateFns.archive.mockResolvedValue({
      data: { data: { ...SENIOR_NURSE, archived_at: "2026-09-24T12:00:00Z" }, status: 200 },
    });
    const { invalidateSpy } = renderTable();

    await user.click(await screen.findByRole("button", { name: /archive senior nurse/i }));

    await waitFor(() => expect(mutateFns.archive).toHaveBeenCalledWith({ id: SENIOR_NURSE.id }));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["/api/v1/positions"] }),
    );
    expect(toast.success).toHaveBeenCalledWith("Position archived");
  });
});
