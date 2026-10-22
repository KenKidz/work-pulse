"use client";

/**
 * Self-service payslip reads. The API scopes both hooks to the signed-in
 * employee: `listMyPayslips` returns issued + superseded rows only (a draft
 * never leaks before finalize) and `getPayslip` answers `404` for a foreign or
 * unknown id. No company-wide payroll visibility is needed.
 */

import { useGetPayslip, useListMyPayslips } from "@workpulse/api-client";

import type { ListMyPayslipsParams } from "../types";

export function useMyPayslips(params?: ListMyPayslipsParams) {
  return useListMyPayslips(params);
}

export function useMyPayslip(id: string) {
  return useGetPayslip(id);
}
