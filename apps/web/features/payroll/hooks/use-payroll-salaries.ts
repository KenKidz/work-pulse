"use client";

/**
 * Salary-record hooks — append-only history, newest `effective_from` first.
 * Creating a record refreshes the history; a run is never recalculated
 * implicitly (the user recalculates explicitly).
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCreateSalary, useListSalaries } from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { invalidatePayrollSalaries } from "../api";
import { payrollErrorMessage } from "../labels";
import type { ListSalariesParams } from "../types";

export type SalaryFieldName = "employee_id" | "amount" | "effective_from" | "root";

export type SalaryFieldErrors = Partial<Record<SalaryFieldName, string>>;

/** Map a stable salary error code onto the field that caused it. */
export function salaryFieldErrors(error: unknown): SalaryFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "salary_effective_date_taken":
        return {
          effective_from: "This employee already has a salary record effective that date.",
        };
      case "employee_not_found":
        return { employee_id: "That employee doesn’t exist in this organization." };
      case "forbidden":
        return { root: "You don’t have permission to add salary records." };
      default:
        return { root: payrollErrorMessage(error) };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function usePayrollSalaries(params?: ListSalariesParams) {
  return useListSalaries(params);
}

export function useSalaryCreate() {
  const queryClient = useQueryClient();
  return useCreateSalary({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollSalaries(queryClient);
        toast.success("Salary record added");
      },
      onError: (error) => toast.error(payrollErrorMessage(error)),
    },
  });
}
