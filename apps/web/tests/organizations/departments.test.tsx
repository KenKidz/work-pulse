/**
 * Departments feature tests (AC9) — the generated api-client layer is mocked;
 * hooks, error mapping, table states and the form dialog are the real thing.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  useArchiveDepartment,
  useCreateDepartment,
  useListDepartments,
  useRestoreDepartment,
  useUpdateDepartment,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { DepartmentsTable } from "@/features/organizations/components/DepartmentsTable";
import type { Department } from "@/features/organizations/types";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", () => ({
  getListDepartmentsQueryKey: () => ["/api/v1/departments"],
  getListPositionsQueryKey: () => ["/api/v1/positions"],
  getListLocationsQueryKey: () => ["/api/v1/locations"],
  useListDepartments: vi.fn(),
  useCreateDepartment: vi.fn(),
  useUpdateDepartment: vi.fn(),
  useArchiveDepartment: vi.fn(),
  useRestoreDepartment: vi.fn(),
}));

const ENGINEERING: Department = {
  id: "00000000-0000-4000-8000-0000000d0001",
  tenant_id: "00000000-0000-4000-8000-000000000001",
  name: "Engineering",
  parent_department_id: null,
  archived_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function listState(items: Department[], overrides: Record<string, unknown> = {}) {
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

/** Wire a mocked generated mutation hook to a controllable mutateAsync fn. */
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
      <DepartmentsTable />
    </QueryClientProvider>,
  );
  return { queryClient, invalidateSpy };
}

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useCreateDepartment, mutateFns.create);
  wireMutation(useUpdateDepartment, mutateFns.update);
  wireMutation(useArchiveDepartment, mutateFns.archive);
  wireMutation(useRestoreDepartment, mutateFns.restore);
  vi.mocked(useListDepartments).mockReturnValue(listState([]) as never);
});

describe("DepartmentsTable", () => {
  it("renders fetched departments", async () => {
    vi.mocked(useListDepartments).mockReturnValue(
      listState([ENGINEERING, { ...ENGINEERING, id: "…d0002", name: "People Ops" }]) as never,
    );

    renderTable();

    expect(await screen.findByText("Engineering")).toBeInTheDocument();
    expect(screen.getByText("People Ops")).toBeInTheDocument();
    expect(screen.getAllByText("Active")).toHaveLength(2);
  });

  it("shows a loading state", () => {
    vi.mocked(useListDepartments).mockReturnValue(
      listState([], { data: undefined, isPending: true }) as never,
    );

    renderTable();

    expect(screen.getByText("Loading departments…")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    vi.mocked(useListDepartments).mockReturnValue(listState([]) as never);

    renderTable();

    expect(await screen.findByText(/No departments yet/i)).toBeInTheDocument();
  });

  it("shows an error state with a retry action", () => {
    vi.mocked(useListDepartments).mockReturnValue(
      listState([], {
        data: undefined,
        isError: true,
        error: new ApiError(500, null, "Request failed with status 500"),
      }) as never,
    );

    renderTable();

    expect(screen.getByRole("alert")).toHaveTextContent("Could not load departments.");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("creates a department, invalidates the list and toasts", async () => {
    const user = userEvent.setup();
    mutateFns.create.mockResolvedValue({
      data: { data: { ...ENGINEERING, name: "Platform" }, status: 201 },
    });
    const { invalidateSpy } = renderTable();

    await user.click(await screen.findByRole("button", { name: /new department/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Name"), "Platform");
    await user.click(screen.getByRole("button", { name: /create department/i }));

    await waitFor(() =>
      expect(mutateFns.create).toHaveBeenCalledWith({
        data: { name: "Platform", parent_department_id: null },
      }),
    );
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["/api/v1/departments"] }),
    );
    expect(toast.success).toHaveBeenCalledWith("Department created");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("renders a 409 name-taken failure as a visible field error plus toast (AC9)", async () => {
    const user = userEvent.setup();
    mutateFns.create.mockRejectedValue(
      new ApiError(409, "department_name_taken", "Department name already taken in this tenant"),
    );
    renderTable();

    await user.click(await screen.findByRole("button", { name: /new department/i }));
    await user.type(screen.getByLabelText("Name"), "Engineering");
    await user.click(screen.getByRole("button", { name: /create department/i }));

    expect(
      await screen.findByText("This name is already used by another department."),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("Department name already taken in this tenant");
    // The dialog stays open so the user can fix the name.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("archives a department and invalidates the list", async () => {
    const user = userEvent.setup();
    vi.mocked(useListDepartments).mockReturnValue(listState([ENGINEERING]) as never);
    mutateFns.archive.mockResolvedValue({
      data: { data: { ...ENGINEERING, archived_at: "2026-09-24T12:00:00Z" }, status: 200 },
    });
    const { invalidateSpy } = renderTable();

    await user.click(await screen.findByRole("button", { name: /archive engineering/i }));

    await waitFor(() => expect(mutateFns.archive).toHaveBeenCalledWith({ id: ENGINEERING.id }));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["/api/v1/departments"] }),
    );
    expect(toast.success).toHaveBeenCalledWith("Department archived");
  });

  it("restores an archived department", async () => {
    const user = userEvent.setup();
    const archived = { ...ENGINEERING, archived_at: "2026-09-20T08:00:00Z" };
    vi.mocked(useListDepartments).mockReturnValue(listState([archived]) as never);
    mutateFns.restore.mockResolvedValue({ data: { data: ENGINEERING, status: 200 } });

    renderTable();

    expect(await screen.findByText("Archived")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /restore engineering/i }));

    await waitFor(() => expect(mutateFns.restore).toHaveBeenCalledWith({ id: ENGINEERING.id }));
    expect(toast.success).toHaveBeenCalledWith("Department restored");
  });

  it("toggles include_archived on the list query", async () => {
    const user = userEvent.setup();
    renderTable();

    const toggle = await screen.findByRole("button", { name: /include archived/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await waitFor(() =>
      expect(useListDepartments).toHaveBeenCalledWith(
        expect.objectContaining({ include_archived: true }),
      ),
    );
  });
});
