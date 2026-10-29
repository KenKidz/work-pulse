/**
 * "My documents" own-profile surface — the current employee resolves through
 * `/employees/me`, the list is scoped to that id, and the panel is read-only
 * for every role (an HR owner gets no write controls here). The generated
 * api-client is mocked wholesale; hooks and components are the real thing.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useGetCurrentEmployee, useListDocuments } from "@workpulse/api-client";

import { MyDocuments } from "@/features/documents/components/MyDocuments";
import type { Document } from "@/features/documents/types";
import viMessages from "@/messages/vi";

import {
  apiError,
  itemState,
  renderWithClient,
  requestPageState,
  wireQuery,
} from "../leave/helpers";

const auth = vi.hoisted(() => ({
  user: {
    role: "employee" as string,
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

const OWN_DOCUMENT: Document = {
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

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { role: "employee", employee_id: EMPLOYEE_ID };
  wireQuery(useGetCurrentEmployee, itemState({ id: EMPLOYEE_ID }));
  wireQuery(useListDocuments, requestPageState([OWN_DOCUMENT]));
});

describe("MyDocuments", () => {
  it("lists the current employee's documents and never renders write controls", async () => {
    // HR is a writer on other surfaces; the own-profile panel stays read-only.
    auth.user = { role: "hr", employee_id: EMPLOYEE_ID };
    renderWithClient(<MyDocuments />);

    expect(await screen.findByText("Employment contract")).toBeInTheDocument();
    expect(useListDocuments).toHaveBeenCalledWith(
      EMPLOYEE_ID,
      expect.objectContaining({ page: 1 }),
      expect.anything(),
    );
    expect(
      screen.getByRole("button", { name: "Download Employment contract" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload document" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete / })).not.toBeInTheDocument();
  });

  it("shows the quiet not-linked state for an account without an employee record", async () => {
    wireQuery(useGetCurrentEmployee, {
      data: undefined,
      isPending: false,
      isError: true,
      error: apiError(404, "employee_not_found", "Employee not found"),
      refetch: vi.fn(),
    });
    renderWithClient(<MyDocuments />);

    expect(
      await screen.findByText(
        "Your account isn't linked to an employee record, so there are no documents to show.",
      ),
    ).toBeInTheDocument();
    expect(useListDocuments).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Upload document" })).not.toBeInTheDocument();
  });

  it("shows a load error with a retry that refetches the employee record", async () => {
    const refetch = vi.fn();
    wireQuery(useGetCurrentEmployee, {
      data: undefined,
      isPending: false,
      isError: true,
      error: apiError(500, "internal_error", "boom"),
      refetch,
    });
    const user = userEvent.setup();
    renderWithClient(<MyDocuments />);

    expect(await screen.findByText("Could not load your employee record.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("renders a stable loading state while the employee resolves", () => {
    wireQuery(useGetCurrentEmployee, {
      data: undefined,
      isPending: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    renderWithClient(<MyDocuments />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading your documents…");
    expect(useListDocuments).not.toHaveBeenCalled();
  });

  it("has the surface translated in Vietnamese", () => {
    expect(viMessages.documents.myDocuments.title).toBe("Tài liệu của tôi");
    expect(viMessages.documents.myDocuments.noEmployee).toBe(
      "Tài khoản của bạn chưa được liên kết với hồ sơ nhân sự nên chưa có tài liệu nào.",
    );
    expect(viMessages.nav.myDocuments).toBe("Tài liệu của tôi");
  });
});
