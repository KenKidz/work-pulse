/**
 * The generated `@workpulse/api-client` surface used by the documents
 * feature, mocked wholesale for the web suites (performance/leave pattern).
 * Every hook is a `vi.fn`; each test wires only what its surface calls, while
 * the real feature hooks, error mapping and components stay in play.
 */

import { vi } from "vitest";

export function createDocumentsApiClientMock() {
  return {
    // Query keys used by the feature invalidation helpers.
    getListDocumentsQueryKey: (employeeId: string, params?: unknown) => [
      `/api/v1/employees/${employeeId}/documents`,
      ...(params ? [params] : []),
    ],
    // Queries and the imperative download call.
    useGetCurrentEmployee: vi.fn(),
    useListDocuments: vi.fn(),
    downloadDocument: vi.fn(),
    // Mutations.
    useCreateDocumentUpload: vi.fn(),
    useCreateDocument: vi.fn(),
    useUpdateDocument: vi.fn(),
    useDeleteDocument: vi.fn(),
  };
}
