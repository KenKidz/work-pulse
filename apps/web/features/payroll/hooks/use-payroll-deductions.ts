"use client";

/**
 * Deduction-line hooks. The list is a bare array (the contract pins no
 * pagination); writes invalidate it and toast the mapped outcome.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  useCreatePayrollDeduction,
  useListPayrollDeductions,
  useUpdatePayrollDeduction,
} from "@workpulse/api-client";
import { ApiError } from "@workpulse/api-client/mutator/custom-fetch";
import { toast } from "sonner";

import { invalidatePayrollDeductions } from "../api";
import { payrollErrorMessage } from "../labels";

export type DeductionFieldName = "code" | "name" | "value" | "root";

export type DeductionFieldErrors = Partial<Record<DeductionFieldName, string>>;

/** Map a stable deduction error code onto the field that caused it. */
export function deductionFieldErrors(error: unknown): DeductionFieldErrors {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "payroll_deduction_code_taken":
        return { code: "This code is already used by another deduction line." };
      case "validation_error":
        return { value: "Choose a value that matches the deduction type." };
      case "payroll_deduction_not_found":
        return { root: "This deduction line is no longer available." };
      case "forbidden":
        return { root: "You don’t have permission to configure deductions." };
      default:
        return { root: payrollErrorMessage(error) };
    }
  }
  return { root: "Something went wrong. Please try again." };
}

export function usePayrollDeductions() {
  return useListPayrollDeductions();
}

export function usePayrollDeductionCreate() {
  const queryClient = useQueryClient();
  return useCreatePayrollDeduction({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollDeductions(queryClient);
        toast.success("Deduction line created");
      },
      onError: (error) => toast.error(payrollErrorMessage(error)),
    },
  });
}

export function usePayrollDeductionUpdate() {
  const queryClient = useQueryClient();
  return useUpdatePayrollDeduction({
    mutation: {
      onSuccess: async () => {
        await invalidatePayrollDeductions(queryClient);
        toast.success("Deduction line updated");
      },
      onError: (error) => toast.error(payrollErrorMessage(error)),
    },
  });
}
