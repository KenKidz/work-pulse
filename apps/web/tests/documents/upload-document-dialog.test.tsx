/**
 * Upload dialog — the two-step flow (intent → storage POST → confirm), the
 * pinned confirm-failure toast, and the local file checks. The generated
 * api-client is mocked wholesale; the storage POST is the real plain fetch.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCreateDocument, useCreateDocumentUpload } from "@workpulse/api-client";
import { toast } from "sonner";

import { UploadDocumentDialog } from "@/features/documents/components/UploadDocumentDialog";

import { apiError, renderWithClient, wireMutation, type MutateFn } from "../leave/helpers";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@workpulse/api-client", async () =>
  (await import("./api-client-mock")).createDocumentsApiClientMock(),
);

const EMPLOYEE_ID = "01900000-0000-7000-8000-0000000000a1";
const UPLOAD_ID = "01900000-0000-7000-8000-0000000000d1";
const DOCUMENT_ID = "01900000-0000-7000-8000-0000000000c1";
const STORAGE_URL = "https://storage.test/upload";

const intent = vi.fn<(variables: unknown) => Promise<unknown>>();
const confirm = vi.fn<(variables: unknown) => Promise<unknown>>();

function uploadIntentState() {
  return {
    data: {
      upload_id: UPLOAD_ID,
      url: STORAGE_URL,
      fields: {
        key: `staging/tenant/${UPLOAD_ID}`,
        policy: "signed-policy",
        "Content-Type": "application/pdf",
        "x-amz-meta-claimed-size": "9",
      },
      expires_at: "2026-10-29T10:10:00Z",
    },
    status: 201,
  };
}

function confirmedDocumentState() {
  return {
    data: {
      id: DOCUMENT_ID,
      employee_id: EMPLOYEE_ID,
      title: "Employment contract",
      category: "contract",
      expiry_date: "2027-12-31",
      filename: "Offer letter.pdf",
      content_type: "application/pdf",
      size_bytes: 9,
      uploaded_by: "01900000-0000-7000-8000-0000000000b1",
      created_at: "2026-10-29T10:00:00Z",
      updated_at: "2026-10-29T10:00:00Z",
    },
    status: 201,
  };
}

function pdfFile() {
  return new File(["%PDF-1.4"], "Offer letter.pdf", { type: "application/pdf" });
}

function renderDialog() {
  const onOpenChange = vi.fn();
  const result = renderWithClient(
    <UploadDocumentDialog open onOpenChange={onOpenChange} employeeId={EMPLOYEE_ID} />,
  );
  return { ...result, onOpenChange };
}

async function fillMetadata(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Title"), "Employment contract");
  await user.click(screen.getByRole("combobox", { name: "Category" }));
  await user.click(await screen.findByRole("option", { name: "Contract" }));
  fireEvent.change(screen.getByLabelText("Expiry date"), {
    target: { value: "2027-12-31" },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  intent.mockResolvedValue(uploadIntentState());
  confirm.mockResolvedValue(confirmedDocumentState());
  wireMutation(useCreateDocumentUpload as unknown as MutateFn, intent);
  wireMutation(useCreateDocument as unknown as MutateFn, confirm);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("UploadDocumentDialog", () => {
  it("uploads to storage, then confirms the metadata and closes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog();
    const file = pdfFile();

    await user.upload(screen.getByLabelText("File"), file);
    await fillMetadata(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(intent).toHaveBeenCalledWith({
        employeeId: EMPLOYEE_ID,
        data: { content_type: "application/pdf", size_bytes: file.size },
      }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(STORAGE_URL);
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body.get("key")).toBe(`staging/tenant/${UPLOAD_ID}`);
    expect(body.get("x-amz-meta-claimed-size")).toBe("9");
    expect(body.get("x-amz-meta-filename")).toBe(encodeURIComponent("Offer letter.pdf"));
    expect((body.get("file") as File).name).toBe("Offer letter.pdf");

    await waitFor(() =>
      expect(confirm).toHaveBeenCalledWith({
        employeeId: EMPLOYEE_ID,
        data: {
          upload_id: UPLOAD_ID,
          title: "Employment contract",
          category: "contract",
          expiry_date: "2027-12-31",
        },
      }),
    );
    expect(toast.success).toHaveBeenCalledWith("Document uploaded");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("shows the pinned toast and keeps the dialog open when confirm fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 204 }));
    confirm.mockRejectedValue(
      apiError(409, "document_upload_not_completed", "Upload not completed"),
    );
    const user = userEvent.setup();
    const { onOpenChange, queryClient } = renderDialog();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await user.upload(screen.getByLabelText("File"), pdfFile());
    await fillMetadata(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(
      await screen.findByText("The upload wasn't completed. Upload the file again."),
    ).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("The upload wasn't completed. Upload the file again.");
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("refuses a file type outside the allowlist before calling the API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderDialog();
    const html = new File(["<html></html>"], "page.html", { type: "text/html" });

    fireEvent.change(screen.getByLabelText("File"), { target: { files: [html] } });
    await fillMetadata(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByText("Choose a PDF, PNG or JPEG file")).toBeInTheDocument();
    expect(intent).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("reports a failed storage POST without confirming", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 403 }));
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog();

    await user.upload(screen.getByLabelText("File"), pdfFile());
    await fillMetadata(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(
      await screen.findByText("The file could not be uploaded. Try again."),
    ).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    // The file stays selected for a retry.
    const input = screen.getByLabelText("File") as HTMLInputElement;
    expect(input.files?.[0]?.name).toBe("Offer letter.pdf");
  });

  it("lets the user close an in-flight upload without confirming", async () => {
    let resolveIntent!: (value: unknown) => void;
    intent.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveIntent = resolve;
        }),
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { onOpenChange } = renderDialog();

    await user.upload(screen.getByLabelText("File"), pdfFile());
    await fillMetadata(user);
    await user.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByRole("button", { name: "Cancel" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);

    resolveIntent(uploadIntentState());
    await waitFor(() => expect(confirm).not.toHaveBeenCalled());
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
