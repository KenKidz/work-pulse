/**
 * "My payslips" self-service surfaces (T11 / AC13). The generated client is
 * mocked; the real hooks, labels and components run. The list must mark issued
 * and superseded rows, and an unknown/foreign payslip id must render the
 * not-found state rather than an error or a blank page.
 */

import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";

import { MyPayslipsTable } from "@/features/payroll/components/MyPayslipsTable";
import { PayslipDetail } from "@/features/payroll/components/PayslipDetail";
import type { Payslip } from "@/features/payroll/types";

import { itemState, renderWithClient, requestPageState, wireQuery } from "../leave/helpers";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@workpulse/api-client", () => ({
  getGetPayslipQueryKey: (id: string) => [`/api/v1/payroll/payslips/${id}`],
  getListMyPayslipsQueryKey: () => ["/api/v1/payroll/payslips"],
  useGetPayslip: vi.fn(),
  useListMyPayslips: vi.fn(),
}));

import { useGetPayslip, useListMyPayslips } from "@workpulse/api-client";

const EMPLOYEE_ID = "00000000-0000-4000-8000-000000000021";

const ISSUED: Payslip = {
  id: "00000000-0000-4000-8000-0000000000a1",
  run_id: "00000000-0000-4000-8000-0000000000b1",
  employee_id: EMPLOYEE_ID,
  period: "2026-09",
  status: "issued",
  base: 4800,
  overtime_pay: 0,
  bonuses: 0,
  deductions: [
    {
      code: "E2E_TAX",
      name: "E2E withholding",
      kind: "percent_of_base",
      value: 10,
      amount: 480,
    },
  ],
  net: 4320,
  issued_at: "2026-10-01T10:00:00Z",
};

const SUPERSEDED: Payslip = {
  ...ISSUED,
  id: "00000000-0000-4000-8000-0000000000a2",
  run_id: "00000000-0000-4000-8000-0000000000b2",
  status: "superseded",
  base: 4600,
  net: 4120,
  issued_at: "2026-09-01T10:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  wireQuery(useListMyPayslips, requestPageState([ISSUED, SUPERSEDED]));
});

describe("MyPayslipsTable", () => {
  it("marks issued and superseded rows and links each to its detail", () => {
    renderWithClient(<MyPayslipsTable />);

    const rows = screen.getAllByTestId("my-payslip-row");
    expect(rows.map((row) => row.getAttribute("data-status"))).toEqual(["issued", "superseded"]);
    expect(rows[0]).toHaveTextContent("September 2026");
    expect(rows[0]).toHaveTextContent("Issued");
    expect(rows[0]).toHaveTextContent("4,320.00");
    expect(rows[1]).toHaveTextContent("Superseded");
    expect(rows[1]).toHaveTextContent("4,120.00");

    expect(
      screen.getByRole("link", { name: "View issued September 2026 payslip" }),
    ).toHaveAttribute("href", `/payroll/my-payslips/${ISSUED.id}`);
    expect(
      screen.getByRole("link", { name: "View superseded September 2026 payslip" }),
    ).toHaveAttribute("href", `/payroll/my-payslips/${SUPERSEDED.id}`);
  });

  it("invites the employee when no payslip has been issued yet", () => {
    wireQuery(useListMyPayslips, requestPageState([]));

    renderWithClient(<MyPayslipsTable />);

    expect(
      screen.getByText("No payslips yet. They appear here after a payroll run is finalized."),
    ).toBeInTheDocument();
  });
});

describe("PayslipDetail", () => {
  it("shows the payslip lines and marks a superseded record", () => {
    wireQuery(useGetPayslip, itemState(SUPERSEDED));

    renderWithClient(<PayslipDetail payslipId={SUPERSEDED.id} />);

    expect(screen.getByRole("heading", { name: "Payslip — September 2026" })).toBeInTheDocument();
    expect(screen.getByText("Superseded")).toBeInTheDocument();
    expect(screen.getByText("This payslip was superseded")).toBeInTheDocument();
    expect(screen.getByText("E2E_TAX")).toBeInTheDocument();
    expect(screen.getAllByText("480.00").length).toBeGreaterThan(0);
    expect(screen.getByText("4,120.00")).toBeInTheDocument();
  });

  it("renders the not-found state for an unknown or foreign payslip id", () => {
    wireQuery(useGetPayslip, {
      data: undefined,
      isPending: false,
      isError: true,
      error: new ApiError(404, "payslip_not_found", "Payslip not found"),
      refetch: vi.fn(),
    });

    renderWithClient(<PayslipDetail payslipId="00000000-0000-4000-8000-0000000000ff" />);

    expect(
      screen.getByText("This payslip doesn’t exist or you don’t have access to it."),
    ).toBeInTheDocument();
  });
});
