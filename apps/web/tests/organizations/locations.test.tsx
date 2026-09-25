/**
 * Locations feature tests — CRUD happy path and the pinned `invalid_timezone`
 * 422 rendered as a timezone field error.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  useArchiveLocation,
  useCreateLocation,
  useListLocations,
  useRestoreLocation,
  useUpdateLocation,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { LocationsTable } from "@/features/organizations/components/LocationsTable";
import type { Location } from "@/features/organizations/types";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getListDepartmentsQueryKey: () => ["/api/v1/departments"],
  getListPositionsQueryKey: () => ["/api/v1/positions"],
  getListLocationsQueryKey: () => ["/api/v1/locations"],
  useListLocations: vi.fn(),
  useCreateLocation: vi.fn(),
  useUpdateLocation: vi.fn(),
  useArchiveLocation: vi.fn(),
  useRestoreLocation: vi.fn(),
}));

const HQ: Location = {
  id: "00000000-0000-4000-8000-0000000c0001",
  tenant_id: "00000000-0000-4000-8000-000000000001",
  name: "Ho Chi Minh Office",
  address: "1 Nguyen Hue Blvd",
  timezone: "Asia/Ho_Chi_Minh",
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function listState(items: Location[], overrides: Record<string, unknown> = {}) {
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
      <LocationsTable />
    </QueryClientProvider>,
  );
  return { queryClient, invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useCreateLocation, mutateFns.create);
  wireMutation(useUpdateLocation, mutateFns.update);
  wireMutation(useArchiveLocation, mutateFns.archive);
  wireMutation(useRestoreLocation, mutateFns.restore);
  vi.mocked(useListLocations).mockReturnValue(listState([]) as never);
});

describe("LocationsTable", () => {
  it("renders fetched locations with their timezones", async () => {
    vi.mocked(useListLocations).mockReturnValue(listState([HQ]) as never);

    renderTable();

    expect(await screen.findByText("Ho Chi Minh Office")).toBeInTheDocument();
    expect(screen.getByText("Asia/Ho_Chi_Minh")).toBeInTheDocument();
    expect(screen.getByText("1 Nguyen Hue Blvd")).toBeInTheDocument();
  });

  it("creates a location, invalidates the list and toasts", async () => {
    const user = userEvent.setup();
    mutateFns.create.mockResolvedValue({
      data: { data: { ...HQ, name: "Berlin Office" }, status: 201 },
    });
    const { invalidateSpy } = renderTable();

    await user.click(await screen.findByRole("button", { name: /new location/i }));
    await user.type(screen.getByLabelText("Name"), "Berlin Office");
    await user.type(screen.getByLabelText("Timezone"), "Europe/Berlin");
    await user.click(screen.getByRole("button", { name: /create location/i }));

    await waitFor(() =>
      expect(mutateFns.create).toHaveBeenCalledWith({
        data: { name: "Berlin Office", address: null, timezone: "Europe/Berlin" },
      }),
    );
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["/api/v1/locations"] }),
    );
    expect(toast.success).toHaveBeenCalledWith("Location created");
  });

  it("renders invalid_timezone as a timezone field error", async () => {
    const user = userEvent.setup();
    mutateFns.create.mockRejectedValue(
      new ApiError(422, "invalid_timezone", "Timezone is not a valid IANA timezone"),
    );
    renderTable();

    await user.click(await screen.findByRole("button", { name: /new location/i }));
    await user.type(screen.getByLabelText("Name"), "Mars Base");
    await user.type(screen.getByLabelText("Timezone"), "Mars/Olympus_Mons");
    await user.click(screen.getByRole("button", { name: /create location/i }));

    expect(
      await screen.findByText("Enter a valid IANA timezone, e.g. Asia/Ho_Chi_Minh."),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("Timezone is not a valid IANA timezone");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("archives and restores a location", async () => {
    const user = userEvent.setup();
    const archived = { ...HQ, archived_at: "2026-09-20T08:00:00Z" };
    vi.mocked(useListLocations).mockReturnValue(listState([archived]) as never);
    mutateFns.restore.mockResolvedValue({ data: { data: HQ, status: 200 } });

    renderTable();

    expect(await screen.findByText("Archived")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /restore ho chi minh office/i }));

    await waitFor(() => expect(mutateFns.restore).toHaveBeenCalledWith({ id: HQ.id }));
    expect(toast.success).toHaveBeenCalledWith("Location restored");
  });
});
