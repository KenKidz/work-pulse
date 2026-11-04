/**
 * The shared employee directory walks every API page once and caches the
 * result. This suite pins the paging and the disabled path against the
 * generated `listEmployees` function.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listEmployees } from "@workpulse/api-client";

import { useEmployeeDirectory } from "@/features/employees/hooks/use-employee-directory";

vi.mock("@workpulse/api-client", () => ({
  listEmployees: vi.fn(),
}));

const A = { id: "employee-a", first_name: "Amara", last_name: "Okafor" };
const B = { id: "employee-b", first_name: "Daniel", last_name: "Park" };

function page(items: unknown[], totalPages: number, page: number) {
  return {
    status: 200,
    data: {
      items,
      page: { page, page_size: 100, total_items: totalPages * 100, total_pages: totalPages },
    },
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useEmployeeDirectory", () => {
  it("reads every page and returns one flat directory", async () => {
    vi.mocked(listEmployees)
      .mockResolvedValueOnce(page([A], 2, 1) as never)
      .mockResolvedValueOnce(page([B], 2, 2) as never);

    const { result } = renderHook(() => useEmployeeDirectory(), { wrapper });

    await waitFor(() => expect(result.current.data).toHaveLength(2));
    expect(result.current.data?.map((employee) => employee.id)).toEqual([A.id, B.id]);
    expect(listEmployees).toHaveBeenCalledWith({ page: 1, page_size: 100 });
    expect(listEmployees).toHaveBeenCalledWith({ page: 2, page_size: 100 });
  });

  it("does not fetch when the caller has no directory access", () => {
    renderHook(() => useEmployeeDirectory(false), { wrapper });

    expect(listEmployees).not.toHaveBeenCalled();
  });
});
