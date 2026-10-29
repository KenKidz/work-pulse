/**
 * Documents panel — HR sees the full row action set, a manager gets a
 * read-only list (the API is the authority), and loading / empty / error /
 * denied / expired states render with the shipped copy. The generated
 * api-client is mocked wholesale; hooks and components are the real thing.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadDocument,
  useCreateDocument,
  useCreateDocumentUpload,
  useDeleteDocument,
  useListDocuments,
  useUpdateDocument,
} from "@workpulse/api-client";

import { DocumentsPanel } from "@/features/documents/components/DocumentsPanel";
import type { Document } from "@/features/documents/types";
import viMessages from "@/messages/vi";

import {
  apiError,
  renderWithClient,
  requestPageState,
  wireMutation,
  wireQuery,
  type MutateFn,
} from "../leave/helpers";

const auth = vi.hoisted(() => ({
  user: {
    role: "hr" as string,
    employee_id: null as string | null,
  },
}));

vi.mock("@/features/auth/components/AuthGate", () => ({
  useAuth: () => ({ user: auth.user }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", async () =>
  (await import("./api-client-mock")).createDocumentsApiClientMock(),
);

const EMPLOYEE_ID = "01900000-0000-7000-8000-0000000000a1";
const UPLOADED_BY = "01900000-0000-7000-8000-0000000000b1";

const CONTRACT: Document = {
  id: "01900000-0000-7000-8000-0000000000c1",
  employee_id: EMPLOYEE_ID,
  title: "Employment contract",
  category: "contract",
  expiry_date: "2027-12-31",
  filename: "employment-contract.pdf",
  content_type: "application/pdf",
  size_bytes: 1024,
  uploaded_by: UPLOADED_BY,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
};

const EXPIRED_CERTIFICATE: Document = {
  id: "01900000-0000-7000-8000-0000000000c2",
  employee_id: EMPLOYEE_ID,
  title: "Forklift licence",
  category: "certification",
  expiry_date: "2025-01-15",
  filename: "forklift-licence.pdf",
  content_type: "application/pdf",
  size_bytes: 2048,
  uploaded_by: UPLOADED_BY,
  created_at: "2025-01-10T09:00:00Z",
  updated_at: "2025-01-10T09:00:00Z",
};

const intentUpload = vi.fn<(variables: unknown) => Promise<unknown>>();
const createDocument = vi.fn<(variables: unknown) => Promise<unknown>>();
const updateDocument = vi.fn<(variables: unknown) => Promise<unknown>>();
const deleteDocument = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { role: "hr", employee_id: null };
  wireQuery(useListDocuments, requestPageState([CONTRACT, EXPIRED_CERTIFICATE]));
  wireMutation(useCreateDocumentUpload as unknown as MutateFn, intentUpload);
  wireMutation(useCreateDocument as unknown as MutateFn, createDocument);
  wireMutation(useUpdateDocument as unknown as MutateFn, updateDocument);
  wireMutation(useDeleteDocument as unknown as MutateFn, deleteDocument);
  (downloadDocument as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    data: { url: "https://storage.test/download", expires_at: "2026-10-29T10:05:00Z" },
    status: 200,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DocumentsPanel", () => {
  it("lists documents with category and expiry, and gives HR every action", async () => {
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(await screen.findByText("Employment contract")).toBeInTheDocument();
    expect(screen.getByText("employment-contract.pdf")).toBeInTheDocument();
    expect(screen.getByText("Contract")).toBeInTheDocument();
    expect(screen.getByText("Forklift licence")).toBeInTheDocument();
    expect(screen.getByText("Expired")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Upload document" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Employment contract" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Employment contract" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Download Employment contract" }),
    ).toBeInTheDocument();
  });

  it("mints a fresh download URL per click and opens it in a new tab", async () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const user = userEvent.setup();
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    await user.click(await screen.findByRole("button", { name: "Download Employment contract" }));

    await waitFor(() => expect(downloadDocument).toHaveBeenCalledWith(EMPLOYEE_ID, CONTRACT.id));
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        "https://storage.test/download",
        "_blank",
        "noopener,noreferrer",
      ),
    );
  });

  it("gives a manager a read-only list on a direct report's record", async () => {
    auth.user = { role: "manager", employee_id: "01900000-0000-7000-8000-0000000000a2" };
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(await screen.findByText("Employment contract")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Download Employment contract" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload document" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete / })).not.toBeInTheDocument();
  });

  it("filters by category and resets to the first page", async () => {
    const user = userEvent.setup();
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    await user.click(await screen.findByRole("combobox", { name: "Filter by category" }));
    await user.click(await screen.findByRole("option", { name: "Certification" }));

    await waitFor(() =>
      expect(useListDocuments).toHaveBeenLastCalledWith(
        EMPLOYEE_ID,
        expect.objectContaining({ category: "certification", page: 1 }),
        expect.anything(),
      ),
    );
  });

  it("offers a way back when a delete leaves the page out of range", async () => {
    wireQuery(useListDocuments, {
      data: {
        data: {
          items: [],
          page: { page: 2, page_size: 20, total_items: 1, total_pages: 1 },
        },
        status: 200,
      },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    const user = userEvent.setup();
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(
      await screen.findByText("This page is empty. The list changed while you were here."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to first page" }));

    await waitFor(() =>
      expect(useListDocuments).toHaveBeenLastCalledWith(
        EMPLOYEE_ID,
        expect.objectContaining({ page: 1 }),
        expect.anything(),
      ),
    );
  });

  it("renders a stable table-shaped loading state", () => {
    wireQuery(useListDocuments, {
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading documents…");
  });

  it("shows the empty state when an employee has no documents", async () => {
    wireQuery(useListDocuments, requestPageState([]));
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(
      await screen.findByText(
        "No documents yet. HR can upload a signed contract, an ID copy or a certification.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the error state with a retry that refetches the list", async () => {
    const refetch = vi.fn();
    wireQuery(useListDocuments, {
      data: undefined,
      isPending: false,
      isError: true,
      error: apiError(500, "internal_error", "boom"),
      refetch,
    });
    const user = userEvent.setup();
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(await screen.findByText("Could not load documents.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("shows the quiet denied state when the caller is out of scope", async () => {
    auth.user = { role: "employee", employee_id: "01900000-0000-7000-8000-0000000000a3" };
    wireQuery(useListDocuments, {
      data: undefined,
      isPending: false,
      isError: true,
      error: apiError(403, "forbidden", "Forbidden"),
      refetch: vi.fn(),
    });
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(
      await screen.findByText("You don't have permission to view this employee's documents."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload document" })).not.toBeInTheDocument();
  });

  it("shows the manager's read-only table inside the employee detail workspace copy", async () => {
    auth.user = { role: "manager", employee_id: "01900000-0000-7000-8000-0000000000a2" };
    renderWithClient(<DocumentsPanel employeeId={EMPLOYEE_ID} />);

    expect(await screen.findByRole("table", { name: "Employee documents" })).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Employee documents" });
    expect(within(table).getByText("Forklift licence")).toBeInTheDocument();
  });

  it("has the documents surface translated in Vietnamese", () => {
    expect(viMessages.documents.title).toBe("Tài liệu");
    expect(viMessages.documents.expired).toBe("Đã hết hạn");
    expect(viMessages.documents.actions.upload).toBe("Tải tài liệu lên");
    expect(viMessages.documents.errors.uploadNotCompleted).toBe(
      "Tải lên chưa hoàn tất. Hãy tải tệp lên lại.",
    );
  });
});
