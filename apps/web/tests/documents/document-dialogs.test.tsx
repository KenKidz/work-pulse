/**
 * Edit and delete dialogs — a metadata update sends only changed fields and
 * toasts; delete confirms first and keeps the dialog open on the pinned
 * `document_not_found` failure.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useDeleteDocument, useUpdateDocument } from "@workpulse/api-client";
import { toast } from "sonner";

import { DeleteDocumentDialog } from "@/features/documents/components/DeleteDocumentDialog";
import { EditDocumentDialog } from "@/features/documents/components/EditDocumentDialog";
import type { Document } from "@/features/documents/types";

import { apiError, renderWithClient, wireMutation, type MutateFn } from "../leave/helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", async () =>
  (await import("./api-client-mock")).createDocumentsApiClientMock(),
);

const EMPLOYEE_ID = "01900000-0000-7000-8000-0000000000a1";

const CONTRACT: Document = {
  id: "01900000-0000-7000-8000-0000000000c1",
  employee_id: EMPLOYEE_ID,
  title: "Employment contract",
  category: "contract",
  expiry_date: "2027-12-31",
  filename: "employment-contract.pdf",
  content_type: "application/pdf",
  size_bytes: 1024,
  uploaded_by: "01900000-0000-7000-8000-0000000000b1",
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
};

const updateDocument = vi.fn<(variables: unknown) => Promise<unknown>>();
const deleteDocument = vi.fn<(variables: unknown) => Promise<unknown>>();

beforeEach(() => {
  vi.clearAllMocks();
  wireMutation(useUpdateDocument as unknown as MutateFn, updateDocument);
  wireMutation(useDeleteDocument as unknown as MutateFn, deleteDocument);
});

describe("EditDocumentDialog", () => {
  it("sends only the changed metadata field and toasts", async () => {
    updateDocument.mockResolvedValue({
      data: { ...CONTRACT, title: "Renewed contract" },
      status: 200,
    });
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderWithClient(
      <EditDocumentDialog
        open
        onOpenChange={onOpenChange}
        employeeId={EMPLOYEE_ID}
        document={CONTRACT}
      />,
    );

    const title = screen.getByLabelText("Title");
    expect(title).toHaveValue("Employment contract");
    await user.clear(title);
    await user.type(title, "Renewed contract");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateDocument).toHaveBeenCalledWith({
        employeeId: EMPLOYEE_ID,
        documentId: CONTRACT.id,
        data: { title: "Renewed contract" },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Document updated");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("keeps the dialog open with inline copy when the update is refused", async () => {
    updateDocument.mockRejectedValue(apiError(404, "document_not_found", "Document not found"));
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderWithClient(
      <EditDocumentDialog
        open
        onOpenChange={onOpenChange}
        employeeId={EMPLOYEE_ID}
        document={CONTRACT}
      />,
    );

    const title = screen.getByLabelText("Title");
    await user.clear(title);
    await user.type(title, "Renewed contract");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("This document is no longer available.")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

describe("DeleteDocumentDialog", () => {
  it("deletes after confirmation and closes", async () => {
    deleteDocument.mockResolvedValue({ data: undefined, status: 204 });
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderWithClient(
      <DeleteDocumentDialog
        open
        onOpenChange={onOpenChange}
        employeeId={EMPLOYEE_ID}
        document={CONTRACT}
      />,
    );

    expect(
      screen.getByText(
        "Delete Employment contract? The file is removed from storage. This can't be undone.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete document" }));

    await waitFor(() =>
      expect(deleteDocument).toHaveBeenCalledWith({
        employeeId: EMPLOYEE_ID,
        documentId: CONTRACT.id,
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Document deleted");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("keeps the dialog open on a second-delete 404", async () => {
    deleteDocument.mockRejectedValue(apiError(404, "document_not_found", "Document not found"));
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderWithClient(
      <DeleteDocumentDialog
        open
        onOpenChange={onOpenChange}
        employeeId={EMPLOYEE_ID}
        document={CONTRACT}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Delete document" }));

    expect(await screen.findByText("This document is no longer available.")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
